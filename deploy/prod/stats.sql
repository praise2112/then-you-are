\pset footer off
\echo '== Visits per day (people, bots apart) =='
select day, sum(views) filter (where device <> 'automated') as people,
       sum(views) filter (where device = 'automated') as bots
from page_views where day > current_date - :days group by day order by day desc;

\echo '== Visits by page and source =='
select section, coalesce(nullif(source, ''), '(direct)') as source, sum(views) as views
from page_views where day > current_date - :days and device <> 'automated'
group by 1, 2 order by views desc limit 25;

\echo '== Visits by country and device =='
select coalesce(nullif(country, ''), '?') as country, device, sum(views) as views
from page_views where day > current_date - :days and device <> 'automated'
group by 1, 2 order by views desc limit 15;

\echo '== Players per day =='
select d::date as day,
       (select count(*) from sessions s where s.created_at::date = d) as new_guests,
       (select count(*) from accounts a where a.created_at::date = d) as sign_ups,
       (select count(distinct se.session_key) from seats se join matches m on m.id = se.match_id
         where se.kind = 'human' and m.created_at::date = d) as players
from generate_series(current_date - :days + 1, current_date, '1 day') as d order by day desc;

\echo '== Sign-ups by provider =='
select provider, count(*) from identities where created_at > now() - make_interval(days => :days)
group by provider order by 2 desc;

\echo '== Returning players (seen a day or more after their first visit) =='
select count(*) filter (where last_seen_at > created_at + interval '1 day') as returning,
       count(*) as all_sessions
from sessions where created_at > now() - make_interval(days => :days);

\echo '== Games by mode and how they ended =='
select kind, count(*) as started,
       count(*) filter (where ended_at is not null and end_reason <> 'abandoned') as finished,
       count(*) filter (where end_reason = 'abandoned') as abandoned,
       round(avg(round_n) filter (where ended_at is not null), 1) as avg_rounds
from matches where created_at > now() - make_interval(days => :days)
group by kind order by started desc;

\echo '== Games against the House: who won =='
select coalesce(s.kind, 'no winner') as winner, count(*)
from matches m left join seats s on s.match_id = m.id and s.seat = m.winner
where m.kind = 'house' and m.ended_at is not null and m.end_reason <> 'abandoned' and m.created_at > now() - make_interval(days => :days)
group by 1 order by 2 desc;

\echo '== Moves and judge outcomes =='
select outcome, count(*) filter (where model_ref is null) as by_people,
       count(*) filter (where model_ref is not null) as by_models
from turns where created_at > now() - make_interval(days => :days)
group by outcome order by 2 desc;

\echo '== Disagree votes on the judge =='
select count(*) as votes, count(distinct match_id) as games
from disagreements d join matches m on m.id = d.match_id
where m.created_at > now() - make_interval(days => :days);

\echo '== Model spend and judge latency per day =='
select ds.day, round(ds.cost_usd::numeric, 3) as cost_usd,
       (select round(avg(latency_ms)) from verdicts v where v.created_at::date = ds.day) as judge_ms
from daily_spend ds where ds.day > current_date - :days order by ds.day desc;
