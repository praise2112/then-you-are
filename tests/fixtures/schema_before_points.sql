create table if not exists sessions (
    session_key text primary key,
    stage_name text not null default 'Challenger',
    created_at timestamptz not null default now()
);

create table if not exists matches (
    id text primary key,
    template_id text not null,
    template_version int not null,
    config jsonb not null,
    seed_token text not null,
    seed_emoji text not null,
    status text not null,
    state_version int not null default 0,
    to_move text not null default 'p1',
    round_n int not null default 1,
    winner text,
    end_reason text,
    is_public boolean not null default true,
    is_curated boolean not null default false,
    created_at timestamptz not null default now(),
    ended_at timestamptz
);

create table if not exists turns (
    id bigserial primary key,
    match_id text not null references matches(id),
    seq int,
    actor text not null,
    move_text text not null,
    layer1_result text,
    outcome text not null,
    live_verdict_id bigint,
    action_id text,
    created_at timestamptz not null default now(),
    unique (match_id, action_id)
);

create table if not exists verdicts (
    id bigserial primary key,
    turn_id bigint references turns(id),
    purpose text not null default 'live',
    judge_model text not null,
    prompt_hash text not null,
    raw_response text not null,
    scoring jsonb,
    host jsonb,
    latency_ms int not null,
    tokens_in int not null default 0,
    tokens_out int not null default 0,
    cost_usd double precision not null default 0,
    created_at timestamptz not null default now()
);

create table if not exists verdict_pairs (
    template_id text not null,
    prev_norm text not null,
    move_norm text not null,
    disagree_count int not null default 0,
    first_seen_at timestamptz not null default now(),
    primary key (template_id, prev_norm, move_norm)
);

alter table sessions add column if not exists list_duels boolean not null default false;
alter table matches alter column is_public set default false;
alter table matches add column if not exists cards text[] not null default '{}';
alter table matches add column if not exists updated_at timestamptz not null default now();
update matches set cards = array[seed_token] where cards = '{}';
alter table turns add column if not exists round_n int not null default 1;
alter table turns add column if not exists model_ref text;

create table if not exists accounts (
    id text primary key,
    provider text not null,
    provider_id text not null,
    display_name text not null,
    avatar_url text not null default '',
    created_at timestamptz not null default now(),
    unique (provider, provider_id)
);
alter table sessions add column if not exists account_id text references accounts(id);

create table if not exists identities (
    provider text not null,
    provider_id text not null,
    account_id text not null references accounts(id),
    created_at timestamptz not null default now(),
    primary key (provider, provider_id)
);
do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_name = 'accounts' and column_name = 'provider'
    ) then
        insert into identities (provider, provider_id, account_id)
        select provider, provider_id, id from accounts on conflict do nothing;
        alter table accounts drop column provider, drop column provider_id;
    end if;
end $$;

create index if not exists sessions_account_id_idx on sessions (account_id);

alter table matches add column if not exists phase text not null default 'write';
create table if not exists guesses (
    id bigserial primary key,
    match_id text not null references matches(id),
    round_n int not null,
    actor text not null,
    picked text not null,
    points int not null,
    awarded_to text not null,
    action_id text,
    created_at timestamptz not null default now(),
    unique (match_id, round_n, actor),
    unique (match_id, action_id)
);

-- One row per seat, in turn order p1..pN. A human seat has a session; a model seat a model ref.
create table if not exists seats (
    match_id text not null references matches(id),
    seat text not null,
    kind text not null check (kind in ('human', 'model')),
    session_key text references sessions(session_key),
    model_ref text,
    points int not null default 0,
    strikes int not null default 0,
    eliminated_at timestamptz,
    primary key (match_id, seat),
    check ((kind = 'human') = (session_key is not null))
);
create index if not exists seats_session_key_idx on seats (session_key);
alter table matches add column if not exists round_n int not null default 1;

do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = current_schema() and table_name = 'matches'
        and column_name = 'p1_session_key'
    ) then
        insert into seats (match_id, seat, kind, session_key, points, strikes)
        select id, 'p1', 'human', p1_session_key, points_p1, strikes_p1 from matches
        on conflict do nothing;
        insert into seats (match_id, seat, kind, model_ref, points, strikes)
        select id, 'p2', 'model', p2_model_ref, points_p2, strikes_p2 from matches
        on conflict do nothing;
        update matches m
        set round_n = case when m.phase = 'guess' then j.judged / 2 else j.judged / 2 + 1 end
        from (
            select m2.id, count(t.id) filter (
                where t.seq is not null and t.outcome in ('accept', 'fail', 'semantic_uncertain')
            ) as judged
            from matches m2 left join turns t on t.match_id = m2.id group by m2.id
        ) j
        where j.id = m.id;
        alter table matches
            drop column p1_session_key, drop column p2_model_ref,
            drop column points_p1, drop column points_p2,
            drop column strikes_p1, drop column strikes_p2;
    end if;
end $$;

-- Stored templates carry the budget in rounds: one move per seat per round.
update matches
set config = (config - 'move_budget')
    || jsonb_build_object('rounds_budget', (config ->> 'move_budget')::int / 2)
where config ? 'move_budget';

-- house: one player against the House, on the standings. friends and open: a table that waits
-- for its seats, joined by invite code or from the lobby, played as an exhibition.
alter table matches add column if not exists kind text not null default 'house';
alter table matches add column if not exists seats_wanted int not null default 2;
alter table matches add column if not exists invite_code text unique;
alter table matches add column if not exists turn_deadline timestamptz;
alter table seats add column if not exists forfeits int not null default 0;
alter table seats add column if not exists submitted_at timestamptz;
alter table seats add column if not exists held_move text;
alter table seats add column if not exists held_round int;
create index if not exists matches_open_idx on matches (status, template_id) where status = 'open';

do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = current_schema() and table_name = 'matches'
        and column_name = 'held_move'
    ) then
        update seats se set held_move = m.held_move
        from matches m where m.id = se.match_id and se.kind = 'model' and m.held_move is not null;
        alter table matches drop column held_move;
    end if;
end $$;

create table if not exists disagreements (
    match_id text not null references matches(id),
    seq int not null,
    session_key text not null,
    primary key (match_id, seq, session_key)
);
alter table sessions add column if not exists revoked boolean not null default false;
alter table sessions add column if not exists last_seen_at timestamptz not null default now();
