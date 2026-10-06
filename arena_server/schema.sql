create table if not exists accounts (
    id text primary key,
    display_name text not null,
    avatar_url text not null default '',
    created_at timestamptz not null default now()
);

create table if not exists identities (
    provider text not null,
    provider_id text not null,
    account_id text not null references accounts(id),
    created_at timestamptz not null default now(),
    primary key (provider, provider_id)
);

create table if not exists sessions (
    session_key text primary key,
    stage_name text not null default 'Challenger',
    account_id text references accounts(id),
    list_duels boolean not null default false,
    revoked boolean not null default false,
    created_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
);
create index if not exists sessions_account_id_idx on sessions (account_id);

-- house: one player against the House, on the standings. friends and open: a table that waits
-- for its seats, joined by invite code or from the lobby, played as an exhibition.
create table if not exists matches (
    id text primary key,
    template_id text not null,
    config jsonb not null,
    seed_emoji text not null,
    cards text[] not null default '{}',
    kind text not null default 'house',
    seats_wanted int not null default 2,
    invite_code text unique,
    status text not null,
    state_version int not null default 0,
    phase text not null default 'write',
    to_move text not null default 'p1',
    round_n int not null default 1,
    turn_deadline timestamptz,
    winner text,
    end_reason text,
    is_public boolean not null default false,
    is_curated boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    ended_at timestamptz
);
create index if not exists matches_open_idx on matches (status, template_id) where status = 'open';

-- One row per seat, in turn order p1..pN. A human seat has a session; a model seat a model ref.
create table if not exists seats (
    match_id text not null references matches(id),
    seat text not null,
    kind text not null check (kind in ('human', 'model')),
    session_key text references sessions(session_key),
    model_ref text,
    points int not null default 0,
    strikes int not null default 0,
    forfeits int not null default 0,
    eliminated_at timestamptz,
    submitted_at timestamptz,
    held_move text,
    held_round int,
    primary key (match_id, seat),
    check ((kind = 'human') = (session_key is not null))
);
create index if not exists seats_session_key_idx on seats (session_key);

-- A refused move has no seq. points is null for a turn without a verdict score.
create table if not exists turns (
    id bigserial primary key,
    match_id text not null references matches(id),
    seq int,
    round_n int not null default 1,
    actor text not null,
    model_ref text,
    move_text text not null,
    layer1_result text,
    outcome text not null,
    points int,
    live_verdict_id bigint,
    action_id text,
    created_at timestamptz not null default now(),
    unique (match_id, action_id)
);

create table if not exists verdicts (
    id bigserial primary key,
    turn_id bigint references turns(id),
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

create table if not exists verdict_pairs (
    template_id text not null,
    prev_norm text not null,
    move_norm text not null,
    disagree_count int not null default 0,
    first_seen_at timestamptz not null default now(),
    primary key (template_id, prev_norm, move_norm)
);

create table if not exists disagreements (
    match_id text not null references matches(id),
    seq int not null,
    session_key text not null,
    primary key (match_id, seq, session_key)
);

-- Runs once, on a database that still has matches.seed_token: stores each turn's points and
-- drops the unused columns.
do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = current_schema() and table_name = 'matches'
        and column_name = 'seed_token'
    ) then
        alter table turns add column points int;
        -- A config with fractional weights is from a build the server cannot read; it plays
        -- such a match with the template's newest weights.
        with whole as (
            select m.id, m.template_id, m.created_at, m.config -> 'rubric' as rubric
            from matches m
            where not exists (
                select 1 from jsonb_array_elements(m.config -> 'rubric') r
                where r ->> 'weight' !~ '^[0-9]+$'
            )
        ), rubrics as (
            select m.id, coalesce(w.rubric, (
                select l.rubric from whole l where l.template_id = m.template_id
                order by l.created_at desc limit 1
            )) as rubric
            from matches m left join whole w on w.id = m.id
        )
        update turns t
        set points = case when t.outcome = 'fail' then 0 else coalesce((
            select sum((s.value)::int * (r ->> 'weight')::int)
            from jsonb_each(v.scoring -> 'scores') s
            join jsonb_array_elements(rb.rubric) r on r ->> 'name' = s.key
        ), 0) end
        from verdicts v, rubrics rb
        where v.id = t.live_verdict_id and v.scoring is not null
        and rb.id = t.match_id and t.seq is not null;
        alter table matches drop column seed_token, drop column template_version;
        alter table verdicts drop column purpose;
    end if;
end $$;
