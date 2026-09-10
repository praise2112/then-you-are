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
    p1_session_key text not null references sessions(session_key),
    p2_model_ref text not null,
    status text not null,
    state_version int not null default 0,
    to_move text not null default 'p1',
    winner text,
    end_reason text,
    points_p1 integer not null default 0,
    points_p2 integer not null default 0,
    strikes_p1 int not null default 0,
    strikes_p2 int not null default 0,
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
alter table matches add column if not exists held_move text;
update matches set cards = array[seed_token] where cards = '{}';
alter table turns add column if not exists round_n int not null default 1;

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
create index if not exists matches_p1_session_key_idx on matches (p1_session_key);
