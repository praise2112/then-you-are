"""The match tables: loading and saving a match, its seats, turns, guesses and verdicts."""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import ValidationError

from arena_core.state import (
    FINISHED,
    IN_PLAY,
    LIVE_STATUSES,
    Actor,
    Guess,
    Match,
    Outcome,
    Turn,
)
from arena_core.template import Seed, Template
from arena_judge.caller import JudgeCall
from arena_server.db import Pool
from arena_server.views import TableKind

ABANDON_WINDOW = timedelta(hours=24)
UNFILLED_WINDOW = timedelta(minutes=10)

log = logging.getLogger(__name__)


class MatchError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class MatchClosed(Exception):
    """The match was abandoned or ended while a turn was still waiting on the judge."""


@dataclass
class SeatRow:
    seat: str
    kind: Literal["human", "model"]
    session_key: str | None
    account_id: str | None
    stage_name: str | None
    submitted: bool
    held_move: str | None
    held_round: int | None
    model_ref: str | None


@dataclass
class Record:
    """What the service keeps about a match beyond the engine's state."""

    # The template the match was created with.
    template: Template
    turn_rows: list[dict]
    seats: list[SeatRow]
    kind: TableKind
    seats_wanted: int
    invite_code: str | None
    turn_deadline: datetime | None
    is_public: bool
    is_curated: bool
    created_at: datetime

    @property
    def humans(self) -> list[SeatRow]:
        return [s for s in self.seats if s.kind == "human"]

    @property
    def models(self) -> frozenset[str]:
        return frozenset(s.seat for s in self.seats if s.kind == "model")

    @property
    def owner(self) -> SeatRow:
        return self.humans[0]

    def row(self, seat: str) -> SeatRow:
        return next(s for s in self.seats if s.seat == seat)


@dataclass
class TurnRow:
    """A turn to store. A refused move has no seq; its `round_n` is the round it was played in,
    when play has moved on since."""

    actor: Actor
    move_text: str
    outcome: Outcome
    seq: int | None = None
    layer1_result: str | None = None
    verdict_id: int | None = None
    action_id: str | None = None
    round_n: int | None = None


async def load_match(
    pool: Pool, match_id: str, templates: dict[str, Template], cache: dict[str, Template]
) -> tuple[Match, Record]:
    """The match and its record. Reads the match's own template unless `cache` holds it, and
    caches it while the match is live; `templates` stands in for one this build cannot read."""
    loaded = await load_matches(pool, [match_id], templates, cache)
    if not loaded:
        raise MatchError(404, "no such match")
    return loaded[0]


async def load_matches(
    pool: Pool, match_ids: list[str], templates: dict[str, Template], cache: dict[str, Template]
) -> list[tuple[Match, Record]]:
    """Each match found and its record, in the order of `match_ids`, as `load_match` reads one."""
    cached = [match_id for match_id in match_ids if match_id in cache]
    async with pool.connection() as conn, conn.transaction():
        # One snapshot for every read, so no turn is newer than its match row.
        await conn.execute("set transaction isolation level repeatable read, read only")
        rows = await (
            await conn.execute(
                "select id, template_id, cards, seed_emoji, status, state_version, to_move, "
                "phase, round_n, winner, end_reason, kind, seats_wanted, invite_code, "
                "turn_deadline, is_public, is_curated, created_at, "
                "case when id = any(%s) then null else config end as config "
                "from matches where id = any(%s)",
                (cached, match_ids),
            )
        ).fetchall()
        turns = await (
            await conn.execute(
                "select t.*, v.scoring, v.host from turns t "
                "left join verdicts v on v.id = t.live_verdict_id "
                "where t.match_id = any(%s) and t.seq is not null order by t.seq",
                (match_ids,),
            )
        ).fetchall()
        guesses = await (
            await conn.execute(
                "select match_id, round_n, actor, picked, points, awarded_to from guesses "
                "where match_id = any(%s) order by id",
                (match_ids,),
            )
        ).fetchall()
        seats = await (
            await conn.execute(
                "select se.*, s.stage_name, s.account_id from seats se "
                "left join sessions s on s.session_key = se.session_key "
                "where se.match_id = any(%s) order by substring(se.seat from 2)::int",
                (match_ids,),
            )
        ).fetchall()

    def by_match(found: list[DictRow]) -> defaultdict[str, list[DictRow]]:
        grouped: defaultdict[str, list[DictRow]] = defaultdict(list)
        for r in found:
            grouped[r["match_id"]].append(r)
        return grouped

    turns_of, guesses_of, seats_of = by_match(turns), by_match(guesses), by_match(seats)
    row_of = {row["id"]: row for row in rows}
    return [
        _assemble(row_of[i], turns_of[i], guesses_of[i], seats_of[i], templates, cache)
        for i in match_ids
        if i in row_of
    ]


def _assemble(
    row: DictRow,
    turns: list[DictRow],
    guesses: list[DictRow],
    seat_rows: list[DictRow],
    templates: dict[str, Template],
    cache: dict[str, Template],
) -> tuple[Match, Record]:
    match_id = row["id"]
    cached = cache.get(match_id)
    template = cached or _stored_template(row, templates)
    if cached is None and row["status"] in LIVE_STATUSES:
        cache[match_id] = template
    match = Match(
        id=row["id"],
        template_id=row["template_id"],
        cards=row["cards"],
        seats=tuple(r["seat"] for r in seat_rows),
        seed_emoji=row["seed_emoji"],
        status=row["status"],
        state_version=row["state_version"],
        to_move=row["to_move"],
        phase=row["phase"],
        round_n=row["round_n"],
        turns=[
            Turn(
                t["seq"],
                t["actor"],
                t["move_text"],
                t["outcome"],
                t["round_n"],
                truth_hit=bool(t["scoring"]) and t["scoring"].get("truth_proximity") == "hit",
                points=t["points"],
            )
            for t in turns
        ],
        guesses=[
            Guess(g["round_n"], g["actor"], g["picked"], g["points"], g["awarded_to"])
            for g in guesses
        ],
        human_seats=tuple(r["seat"] for r in seat_rows if r["kind"] == "human"),
        strikes={r["seat"]: r["strikes"] for r in seat_rows},
        points={r["seat"]: r["points"] for r in seat_rows},
        forfeits={r["seat"]: r["forfeits"] for r in seat_rows},
        eliminated=[
            r["seat"]
            for r in sorted(seat_rows, key=lambda r: r["eliminated_at"] or row["created_at"])
            if r["eliminated_at"]
        ],
        winner=row["winner"],
        end_reason=row["end_reason"],
    )
    rec = Record(
        template=template,
        turn_rows=turns,
        seats=[
            SeatRow(
                seat=r["seat"],
                kind=r["kind"],
                session_key=r["session_key"],
                account_id=r["account_id"],
                stage_name=r["stage_name"],
                submitted=r["submitted_at"] is not None,
                held_move=r["held_move"],
                held_round=r["held_round"],
                model_ref=r["model_ref"],
            )
            for r in seat_rows
        ],
        kind=row["kind"],
        seats_wanted=row["seats_wanted"],
        invite_code=row["invite_code"],
        turn_deadline=row["turn_deadline"],
        is_public=row["is_public"],
        is_curated=row["is_curated"],
        created_at=row["created_at"],
    )
    return match, rec


def _stored_template(row: DictRow, templates: dict[str, Template]) -> Template:
    if row["config"]:
        try:
            return Template.model_validate(row["config"])
        except ValidationError:
            log.warning("match %s stored a template this build cannot read", row["id"])
    return templates[row["template_id"]]


async def update_match(conn: AsyncConnection[DictRow], match: Match) -> None:
    """Writes the match state. A match already closed in the database stays closed:
    the write is refused with MatchClosed."""
    result = await conn.execute(
        "update matches set status = %s, state_version = %s, to_move = %s, phase = %s, "
        "round_n = %s, winner = %s, end_reason = %s, updated_at = now(), "
        "turn_deadline = case when %s = any(%s) then null else turn_deadline end, "
        "ended_at = case when %s = 'ended' "
        "and ended_at is null then now() else ended_at end "
        "where id = %s and status <> all(%s)",
        (
            match.status,
            match.state_version,
            match.to_move,
            match.phase,
            match.round_n,
            match.winner,
            match.end_reason,
            match.status,
            list(FINISHED),
            match.status,
            match.id,
            list(FINISHED),
        ),
    )
    if result.rowcount == 0:
        raise MatchClosed(match.id)
    seats = list(match.seats)
    await conn.execute(
        "update seats as se set points = v.points, strikes = v.strikes, "
        "forfeits = v.forfeits, eliminated_at = case when v.out "
        "then coalesce(se.eliminated_at, now()) end "
        "from unnest(%s::text[], %s::int[], %s::int[], %s::int[], %s::bool[]) "
        "as v(seat, points, strikes, forfeits, out) "
        "where se.match_id = %s and se.seat = v.seat",
        (
            seats,
            [match.points[s] for s in seats],
            [match.strikes[s] for s in seats],
            [match.forfeits[s] for s in seats],
            [s in match.eliminated for s in seats],
            match.id,
        ),
    )


async def save_match(pool: Pool, match: Match) -> None:
    async with pool.connection() as conn, conn.transaction():
        await update_match(conn, match)


async def store_turns(pool: Pool, match: Match, *turns: TurnRow) -> None:
    """One transaction: the new match state and the turns that produced it."""
    async with pool.connection() as conn, conn.transaction():
        await update_match(conn, match)
        for turn in turns:
            await _insert_turn(conn, match, turn)


async def _insert_turn(conn: AsyncConnection[DictRow], match: Match, turn: TurnRow) -> None:
    # A refused move has no seq and no turn in the match.
    played = match.turns[turn.seq - 1] if turn.seq is not None else None
    round_n = turn.round_n
    if round_n is None:
        round_n = played.round_n if played else match.round_n
    row = await (
        await conn.execute(
            "insert into turns (match_id, seq, actor, move_text, layer1_result, outcome, "
            "live_verdict_id, action_id, round_n, points, model_ref) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "(select model_ref from seats where match_id = %s and seat = %s)) returning id",
            (
                match.id,
                turn.seq,
                turn.actor,
                turn.move_text,
                turn.layer1_result,
                turn.outcome,
                turn.verdict_id,
                turn.action_id,
                round_n,
                played.points if played else None,
                match.id,
                turn.actor,
            ),
        )
    ).fetchone()
    assert row is not None
    if turn.verdict_id is not None:
        await conn.execute(
            "update verdicts set turn_id = %s where id = %s", (row["id"], turn.verdict_id)
        )


async def store_guesses(
    pool: Pool, match: Match, guesses: list[Guess], action_id: str | None
) -> None:
    """One transaction: the new match state and the calls that produced it."""
    async with pool.connection() as conn, conn.transaction():
        await update_match(conn, match)
        for guess in guesses:
            await conn.execute(
                "insert into guesses (match_id, round_n, actor, picked, points, awarded_to, "
                "action_id) values (%s, %s, %s, %s, %s, %s, %s)",
                (
                    match.id,
                    guess.round_n,
                    guess.actor,
                    guess.picked,
                    guess.points,
                    guess.awarded_to,
                    action_id,
                ),
            )


async def insert_match(
    conn: AsyncConnection[DictRow],
    match_id: str,
    template: Template,
    cards: list[Seed],
    status: str,
    is_public: bool,
    kind: TableKind,
    seats_wanted: int,
    invite_code: str | None,
) -> None:
    await conn.execute(
        "insert into matches (id, template_id, config, seed_emoji, cards, status, "
        "is_public, kind, seats_wanted, invite_code) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            match_id,
            template.slug,
            template.model_dump_json(),
            cards[0].opening_emoji,
            [c.opening_token for c in cards],
            status,
            is_public,
            kind,
            seats_wanted,
            invite_code,
        ),
    )


async def insert_seat(
    conn: AsyncConnection[DictRow],
    match_id: str,
    seat: str,
    kind: str,
    session_key: str | None,
    model_ref: str | None,
) -> None:
    await conn.execute(
        "insert into seats (match_id, seat, kind, session_key, model_ref) "
        "values (%s, %s, %s, %s, %s)",
        (match_id, seat, kind, session_key, model_ref),
    )


async def insert_verdict(pool: Pool, call: JudgeCall, judge_model: str) -> int:
    response = call.response
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "insert into verdicts (judge_model, prompt_hash, raw_response, scoring, host, "
                "latency_ms, tokens_in, tokens_out, cost_usd) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
                (
                    judge_model,
                    call.prompt_hash,
                    call.raw or "\n".join(call.attempts),
                    response.scoring.model_dump_json() if response else None,
                    response.host.model_dump_json() if response else None,
                    call.latency_ms,
                    call.tokens_in,
                    call.tokens_out,
                    call.cost_usd,
                ),
            )
        ).fetchone()
        assert row is not None
    return row["id"]


async def stamp_badges(pool: Pool, verdict_id: int, badges: list[str]) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update verdicts set host = host || jsonb_build_object('badges', %s::jsonb) "
            "where id = %s",
            (json.dumps(badges), verdict_id),
        )


async def set_status(pool: Pool, match_id: str, status: str) -> None:
    async with pool.connection() as conn:
        result = await conn.execute(
            "update matches set status = %s, updated_at = now() "
            "where id = %s and status <> all(%s)",
            (status, match_id, list(FINISHED)),
        )
    if result.rowcount == 0:
        raise MatchClosed(match_id)


async def start_table(pool: Pool, match_id: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update matches set status = 'active', updated_at = now() "
            "where id = %s and status = 'open'",
            (match_id,),
        )


async def set_deadline(pool: Pool, match_id: str, deadline: datetime | None) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update matches set turn_deadline = %s where id = %s", (deadline, match_id)
        )


async def set_submitted(pool: Pool, match_id: str, seat: str, submitted: bool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update seats set submitted_at = case when %s then now() end "
            "where match_id = %s and seat = %s",
            (submitted, match_id, seat),
        )


async def hold_move(pool: Pool, match_id: str, seat: str, text: str | None, round_n: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update seats set held_move = %s, held_round = %s where match_id = %s and seat = %s",
            (text, round_n, match_id, seat),
        )


async def set_model_ref(pool: Pool, match_id: str, seat: str, model_ref: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update seats set model_ref = %s where match_id = %s and seat = %s",
            (model_ref, match_id, seat),
        )


async def set_public(pool: Pool, match_id: str, public: bool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "update matches set is_public = %s, is_curated = is_curated and %s where id = %s",
            (public, public, match_id),
        )


async def set_curated(pool: Pool, match_id: str, curated: bool) -> None:
    async with pool.connection() as conn:
        result = await conn.execute(
            "update matches set is_curated = %s, is_public = is_public or %s "
            "where id = %s and status = 'ended'",
            (curated, curated, match_id),
        )
        if result.rowcount == 0:
            raise MatchError(404, "no finished match with that id")


async def match_is_live(pool: Pool, match_id: str) -> bool:
    """Whether the match exists and can still change."""
    async with pool.connection() as conn:
        row = await (
            await conn.execute("select status from matches where id = %s", (match_id,))
        ).fetchone()
    return row is not None and row["status"] in LIVE_STATUSES


async def action_seen(pool: Pool, match_id: str, action_id: str) -> bool:
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "select 1 from turns where match_id = %s and action_id = %s "
                "union all select 1 from guesses where match_id = %s and action_id = %s",
                (match_id, action_id, match_id, action_id),
            )
        ).fetchone()
    return row is not None


async def last_refusal(pool: Pool, match_id: str, actor: str, round_n: int) -> DictRow | None:
    """The seat's latest refused move in the round: its outcome, Layer 1 reason and host."""
    async with pool.connection() as conn:
        return await (
            await conn.execute(
                "select t.outcome, t.layer1_result, v.host from turns t "
                "left join verdicts v on v.id = t.live_verdict_id "
                "where t.match_id = %s and t.actor = %s and t.round_n = %s "
                "and t.seq is null order by t.id desc limit 1",
                (match_id, actor, round_n),
            )
        ).fetchone()


async def add_disagreement(
    pool: Pool,
    match_id: str,
    seq: int,
    session_key: str,
    template_id: str,
    prev_norm: str,
    move_norm: str,
) -> None:
    """One vote per session and move; a repeat vote counts nothing."""
    async with pool.connection() as conn, conn.transaction():
        voted = await conn.execute(
            "insert into disagreements (match_id, seq, session_key) values (%s, %s, %s) "
            "on conflict do nothing",
            (match_id, seq, session_key),
        )
        if voted.rowcount == 0:
            return
        await conn.execute(
            "insert into verdict_pairs (template_id, prev_norm, move_norm, disagree_count) "
            "values (%s, %s, %s, 1) on conflict (template_id, prev_norm, move_norm) "
            "do update set disagree_count = verdict_pairs.disagree_count + 1",
            (template_id, prev_norm, move_norm),
        )


async def invite_match_id(pool: Pool, invite_code: str) -> str | None:
    async with pool.connection() as conn:
        row = await (
            await conn.execute("select id from matches where invite_code = %s", (invite_code,))
        ).fetchone()
    return row["id"] if row else None


async def joinable_ids(pool: Pool, template_id: str, session_key: str) -> list[str]:
    """The oldest open tables for the game where neither the session nor its account sits."""
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "select m.id from matches m where m.status = 'open' and m.kind = 'open' "
                "and m.template_id = %s and not exists (select 1 from seats se "
                "join sessions s on s.session_key = se.session_key "
                "where se.match_id = m.id and (s.session_key = %s or s.account_id = "
                "(select account_id from sessions where session_key = %s))) "
                "order by m.created_at limit 5",
                (template_id, session_key, session_key),
            )
        ).fetchall()
    return [row["id"] for row in rows]


async def open_table_rows(pool: Pool) -> list[DictRow]:
    """Every table still filling, oldest first, with its seats taken and its host's name."""
    async with pool.connection() as conn:
        return await (
            await conn.execute(
                "select m.id, m.template_id, m.seats_wanted, m.created_at, m.invite_code, "
                "(select count(*) from seats se where se.match_id = m.id) as taken, "
                "(select s.stage_name from seats se join sessions s "
                "on s.session_key = se.session_key "
                "where se.match_id = m.id and se.seat = 'p1') as host_name "
                "from matches m where m.status = 'open' and m.kind = 'open' "
                "order by m.created_at"
            )
        ).fetchall()


async def live_public_ids(pool: Pool) -> list[str]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "select id from matches where is_public and status = any(%s) "
                "and created_at > now() - interval '2 hours' "
                "order by created_at desc limit 6",
                (list(IN_PLAY),),
            )
        ).fetchall()
    return [row["id"] for row in rows]


async def ended_count(pool: Pool) -> int:
    async with pool.connection() as conn:
        row = await (
            await conn.execute("select count(*) as n from matches where status = 'ended'")
        ).fetchone()
    return row["n"] if row else 0


async def replay_ids(pool: Pool, sort: Literal["curated", "newest", "longest"]) -> list[str]:
    where = "status = 'ended' and is_public"
    if sort == "curated":
        where += " and is_curated"
    if sort == "longest":
        where += " and ended_at > now() - interval '7 days'"
    order = (
        "(select count(*) from turns t where t.match_id = m.id and t.seq is not null) desc"
        if sort == "longest"
        else "ended_at desc"
    )
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(f"select id from matches m where {where} order by {order} limit 12")
        ).fetchall()
    return [row["id"] for row in rows]


async def reset_for_restart(pool: Pool) -> list[str]:
    """Hands every move waiting on the judge back to its seat. Returns the active matches."""
    async with pool.connection() as conn:
        await conn.execute(
            "update matches set status = 'active', updated_at = now() "
            "where status in ('awaiting_judgment', 'paused')"
        )
        await conn.execute("update seats set submitted_at = null where submitted_at is not null")
        rows = await (
            await conn.execute("select id from matches where status = 'active'")
        ).fetchall()
    return [row["id"] for row in rows]


async def close_idle(pool: Pool) -> tuple[list[str], list[str]]:
    """Abandons every match idle past ABANDON_WINDOW. Returns their ids, and the ids of the
    tables still unfilled past UNFILLED_WINDOW."""
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "update matches set status = 'abandoned', end_reason = 'abandoned', "
                "turn_deadline = null, ended_at = now(), updated_at = now() "
                "where status = any(%s) and updated_at < now() - %s returning id",
                (list(IN_PLAY), ABANDON_WINDOW),
            )
        ).fetchall()
        stale = await (
            await conn.execute(
                "select id from matches where status = 'open' and created_at < now() - %s",
                (UNFILLED_WINDOW,),
            )
        ).fetchall()
    return [row["id"] for row in rows], [row["id"] for row in stale]


async def close_unfilled(pool: Pool, match_id: str) -> bool:
    """Abandons a table still filling. Returns False when it started or closed meanwhile."""
    async with pool.connection() as conn:
        closed = await conn.execute(
            "update matches set status = 'abandoned', end_reason = 'unfilled', "
            "ended_at = now(), updated_at = now() where id = %s and status = 'open'",
            (match_id,),
        )
    return closed.rowcount != 0


async def overdue_ids(pool: Pool) -> list[str]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "select id from matches where status = 'active' and turn_deadline < now()"
            )
        ).fetchall()
    return [row["id"] for row in rows]
