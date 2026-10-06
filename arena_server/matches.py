"""Match service: creates duels and tables, seats players, runs human and model turns, keeps
the turn clock, persists everything."""

import asyncio
import hashlib
import json
import logging
import secrets
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import ValidationError

from arena_core.state import (
    JUDGED,
    STANDING,
    Actor,
    Guess,
    Match,
    StaleVersionError,
    Turn,
    apply_guess,
    apply_ruling,
    deal,
    forfeit_turn,
    layer1,
    normalize,
    repeats_the_round,
    resign,
    skip_guess,
    transcript,
    weighted_total,
)
from arena_core.template import Seed, Template
from arena_judge.caller import CallError, JudgeCall, ModelCaller, ModelSpec
from arena_judge.prompt import clean_move
from arena_judge.schema import (
    GuessOpened,
    GuessOption,
    GuessView,
    HostPayload,
    JudgePaused,
    JudgeResponse,
    JudgeResumed,
    JudgeStarted,
    MatchEnded,
    MatchStarted,
    MoveToken,
    Outcome,
    RoundRevealed,
    Ruling,
    ScoringPayload,
    SeatJoined,
    SeatSubmitted,
    TurnChanged,
    TurnRejected,
    route_outcome,
)
from arena_server.auth import account_of, new_session_key
from arena_server.db import Pool
from arena_server.events import EventBus
from arena_server.leaderboard import account_streaks
from arena_server.presence import Lobby, Presence, TurnNudge
from arena_server.views import (
    AccountView,
    MatchSnapshot,
    OpenDuel,
    Replay,
    RoundView,
    SeatView,
    SessionView,
    StageView,
    TableKind,
    TableView,
    TurnView,
)

PAUSE_BACKOFF_S = (5, 10, 20, 30)
# A judge call refused for credentials or credit will not heal on its own; retry slowly.
BILLING_STATUSES = (401, 402, 403)
BILLING_RETRY_S = 60
# A move whose judge has not ruled by then goes back: a human may play again, the House loses it.
JUDGE_GIVE_UP_S = 120
ABANDON_WINDOW = timedelta(hours=24)
UNFILLED_WINDOW = timedelta(minutes=10)
STREAM_LINGER_S = 300
# Human seats get a clock only when two or more humans share the table.
TURN_CLOCK = timedelta(seconds=120)
WRITE_CLOCK = timedelta(seconds=180)
CALL_CLOCK = timedelta(seconds=60)
GRACE = timedelta(seconds=30)
MODEL_REWRITES = 2
# A House call that fails is tried once more before the fallback takes the seat.
HOUSE_ATTEMPTS = 2
HOUSE_RETRY_S = 3
REPEAT_TEXT = "Someone at the table already wrote exactly that. Try another."


log = logging.getLogger(__name__)


class MatchError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class MatchClosed(Exception):
    """The match was abandoned or ended while a turn was still waiting on the judge."""


class HouseStuck(Exception):
    """The House could not produce a legal answer, even its default move."""


class JudgeGaveUp(Exception):
    """The judge gave no ruling within JUDGE_GIVE_UP_S."""


@dataclass
class Judged:
    outcome: Outcome
    response: JudgeResponse
    verdict_id: int


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


@dataclass
class Record:
    """What the service keeps about a match beyond the engine's state."""

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

    @property
    def clocked(self) -> bool:
        return len(self.humans) >= 2

    def row(self, seat: str) -> SeatRow:
        return next(s for s in self.seats if s.seat == seat)


class MatchService:
    def __init__(
        self,
        pool: Pool,
        bus: EventBus,
        caller: ModelCaller,
        templates: dict[str, Template],
        opponent_ref: str,
        opponent_name: str,
        judge_model: str,
        public_base_url: str,
        presence: Presence,
        house_slots: int = 0,
        fallback: tuple[str, ModelSpec] | None = None,
    ):
        self.pool = pool
        self.bus = bus
        self.caller = caller
        self.templates = templates
        self.opponent_ref = opponent_ref
        self.opponent_name = opponent_name
        self.judge_model = judge_model
        self.public_base_url = public_base_url
        self.presence = presence
        self.house_slots = house_slots
        # The hosted model that takes a House seat for the rest of a match once the House fails.
        self.fallback = fallback
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Quick match searches and creates under one lock per game.
        self.seating: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.tasks: set[asyncio.Task] = set()
        # Showcase model seats writing for a round: (match, seat, round).
        self.answering: set[tuple[str, str, int]] = set()
        # Each match plays the template it was created with, parsed once from its row.
        self.match_templates: dict[str, Template] = {}
        # The llama-server slot each House seat's conversation is pinned to: (match, seat).
        self.slots: dict[tuple[str, str], int] = {}
        self.judge_fault: str | None = None

    def template_of(self, match: Match) -> Template:
        return self.match_templates.get(match.id) or self.templates[match.template_id]

    def _forget(self, match_id: str) -> None:
        """Drops the per-match memory once a match is over; the event stream lingers so a
        client can still read the ending."""
        self.locks.pop(match_id, None)
        self.match_templates.pop(match_id, None)
        for key in [k for k in self.slots if k[0] == match_id]:
            del self.slots[key]
        asyncio.get_running_loop().call_later(STREAM_LINGER_S, self.bus.forget, match_id)

    async def recover(self) -> None:
        """After a restart: a move waiting on the judge is lost, so the player resubmits; model
        seats finish any turn they owed, and a clocked seat gets a fresh deadline."""
        async with self.pool.connection() as conn:
            await conn.execute(
                "update matches set status = 'active', updated_at = now() "
                "where status in ('awaiting_judgment', 'paused')"
            )
            await conn.execute(
                "update seats set submitted_at = null where submitted_at is not null"
            )
            # Only a model seat to move or a clocked table has anything to resume.
            rows = await (
                await conn.execute(
                    "select m.id from matches m where m.status = 'active' and (exists "
                    "(select 1 from seats se where se.match_id = m.id and se.seat = m.to_move "
                    "and se.kind = 'model') or (select count(*) from seats se "
                    "where se.match_id = m.id and se.kind = 'human') >= 2)"
                )
            ).fetchall()
        for row in rows:
            self._spawn(self._resume(row["id"]))

    async def _resume(self, match_id: str) -> None:
        async with self.locks[match_id]:
            match, rec = await self._load(match_id)
            if match.status != "active":
                return
            template = self.template_of(match)
            if template.mode == "escalation":
                await self._after_turn(match, template, rec)
                return
            # Model seats write again when a player next opens or answers this match.
            await self._set_clock(match, rec, template)

    # Sessions

    async def ensure_session(
        self, session_key: str | None, stage_name: str | None, list_duels: bool | None = None
    ) -> str:
        key = session_key or new_session_key()
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into sessions (session_key, stage_name, list_duels) "
                "values (%s, %s, coalesce(%s, false)) on conflict (session_key) do update "
                "set stage_name = coalesce(%s, sessions.stage_name), "
                "list_duels = coalesce(%s, sessions.list_duels)",
                (
                    key,
                    stage_name or "Challenger",
                    list_duels,
                    stage_name,
                    list_duels,
                ),
            )
        return key

    async def session_view(self, session_key: str | None) -> SessionView:
        if session_key:
            async with self.pool.connection() as conn:
                row = await (
                    await conn.execute(
                        "select s.stage_name, s.list_duels, a.id, a.display_name, a.avatar_url, "
                        "(select coalesce(array_agg(i.provider order by i.created_at), '{}') "
                        "from identities i where i.account_id = a.id) as providers "
                        "from sessions s left join accounts a on a.id = s.account_id "
                        "where s.session_key = %s",
                        (session_key,),
                    )
                ).fetchone()
                account = None
                if row and row["id"]:
                    streak, best = await account_streaks(conn, row["id"])
                    account = AccountView(
                        id=row["id"],
                        providers=row["providers"],
                        display_name=row["display_name"],
                        avatar_url=row["avatar_url"],
                        streak=streak,
                        best_streak=best,
                    )
            if row:
                return SessionView(
                    stage_name=row["stage_name"],
                    list_duels=row["list_duels"],
                    account=account,
                    open_duels=[
                        await self._open_duel(m, session_key)
                        for m in await self._open_ids(session_key)
                    ],
                )
        return SessionView(stage_name="Challenger", list_duels=False)

    async def _open_ids(
        self, session_key: str, template_id: str | None = None, kind: TableKind | None = None
    ) -> list[str]:
        """Unfinished matches where the session, or any session on its account, holds a live
        seat."""
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "select distinct m.id, m.created_at from matches m "
                    "join seats se on se.match_id = m.id "
                    "join sessions s on s.session_key = se.session_key "
                    "where m.status in ('open', 'active', 'awaiting_judgment', 'paused') "
                    "and se.eliminated_at is null "
                    "and (s.session_key = %s or s.account_id = "
                    "(select account_id from sessions where session_key = %s)) "
                    "and (%s::text is null or m.template_id = %s) "
                    "and (%s::text is null or m.kind = %s) order by m.created_at",
                    (session_key, session_key, template_id, template_id, kind, kind),
                )
            ).fetchall()
        return [row["id"] for row in rows]

    async def _open_duel(self, match_id: str, session_key: str) -> OpenDuel:
        match, rec = await self._load(match_id)
        template = self.template_of(match)
        mine = await self._seat_of(rec, session_key)
        if match.status == "open":
            line = f"waiting for {rec.seats_wanted - len(rec.seats)} more"
        elif template.mode == "showcase":
            line = f"round {min(match.round_n, template.rounds_budget)} of "
            line += f"{template.rounds_budget}, {match.card}"
            if match.phase == "guess" and mine in match.owed_guesses():
                line += ", your call"
        else:
            form = match.standing_form
            if form.lower().startswith(template.move_constraints.prefix.lower()):
                form = form[len(template.move_constraints.prefix) :]
            line = f"round {match.round_n}, {form.rstrip('.')} stands"
            if rec.clocked and match.to_move == mine:
                line += ", your move"
        return OpenDuel(id=match.id, title=template.title, line=line)

    # Creating and seating

    async def create(
        self,
        session_key: str,
        template_id: str,
        seed_token: str | None = None,
        kind: TableKind = "house",
        seats: int = 2,
    ) -> MatchSnapshot:
        """A House duel starts at once. A table for friends or for anyone waits for its seats;
        its creator holds the first one."""
        template = self.templates.get(template_id)
        if template is None:
            raise MatchError(404, "no such template")
        first = template.seed_named(seed_token) if seed_token else None
        if seed_token and first is None:
            raise MatchError(422, "that opening is not in this game")
        if kind == "house":
            seats = 2
        elif not template.num_players.min <= seats <= template.num_players.max:
            raise MatchError(
                422,
                f"{template.title} seats {template.num_players.min} to "
                f"{template.num_players.max} players",
            )
        open_ids = await self._open_ids(session_key, template.slug, kind)
        if open_ids:
            return await self.snapshot(open_ids[0], session_key)
        cards = deal(template, secrets.SystemRandom(), first, template.revealed_card)
        match_id = secrets.token_urlsafe(8)
        listed = (await self.session_view(session_key)).list_duels
        house = kind == "house"
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "insert into matches (id, template_id, template_version, config, seed_token, "
                "seed_emoji, cards, status, is_public, kind, seats_wanted, invite_code) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    match_id,
                    template.slug,
                    template.schema_version,
                    template.model_dump_json(),
                    cards[0].opening_token,
                    cards[0].opening_emoji,
                    [c.opening_token for c in cards],
                    "active" if house else "open",
                    listed,
                    kind,
                    seats,
                    None if house else secrets.token_urlsafe(6),
                ),
            )
            await self._insert_seat(conn, match_id, "p1", "human", session_key)
            if house:
                await self._insert_seat(conn, match_id, "p2", "model", None)
        if house and template.mode == "showcase":
            match, rec = await self._load(match_id)
            self._start_model_answers(match, rec)
        if not house:
            await self._broadcast_lobby()
        return await self.snapshot(match_id, session_key)

    async def _insert_seat(
        self, conn: Any, match_id: str, seat: str, kind: str, session_key: str | None
    ) -> None:
        await conn.execute(
            "insert into seats (match_id, seat, kind, session_key, model_ref) "
            "values (%s, %s, %s, %s, %s)",
            (
                match_id,
                seat,
                kind,
                session_key,
                self.opponent_ref if kind == "model" else None,
            ),
        )
        if kind == "model":
            self._spawn(self.caller.wake_opponent())

    async def join(self, invite_code: str, session_key: str) -> str:
        """Seats the session at the table behind an invite code. Returns the match id."""
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("select id from matches where invite_code = %s", (invite_code,))
            ).fetchone()
        if row is None:
            raise MatchError(404, "no table with that invite")
        await self._fill_seat(row["id"], session_key, "human")
        return row["id"]

    async def quick_match(self, session_key: str, template_id: str, seats: int) -> str:
        """Takes a seat at the oldest open table for the game, or opens one. Returns its id."""
        if template_id not in self.templates:
            raise MatchError(404, "no such template")
        async with self.seating[template_id]:
            mine = await self._open_ids(session_key, template_id, "open")
            if mine:
                return mine[0]
            async with self.pool.connection() as conn:
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
            for row in rows:
                try:
                    await self._fill_seat(row["id"], session_key, "human")
                except MatchError as e:
                    # Filled or closed by an invite or the House since it was listed.
                    if e.status != 409:
                        raise
                    continue
                return row["id"]
            snap = await self.create(session_key, template_id, kind="open", seats=seats)
            return snap.id

    async def add_house(self, match_id: str, session_key: str) -> None:
        """The table's creator fills the next open seat with the House."""
        await self._fill_seat(match_id, session_key, "model")

    async def _fill_seat(
        self, match_id: str, session_key: str, kind: Literal["human", "model"]
    ) -> None:
        """Seats the session, or for its creator the House, at the next seat of a filling table.
        A session already seated is left where it is."""
        async with self.locks[match_id]:
            match, rec = await self._load(match_id)
            seated = await self._seat_of(rec, session_key)
            if kind == "human" and seated:
                return
            if kind == "model" and seated != rec.owner.seat:
                raise MatchError(403, "only the table's creator can add the House")
            if match.status != "open":
                raise MatchError(409, "this table has already started")
            seat = f"p{len(rec.seats) + 1}"
            async with self.pool.connection() as conn:
                await self._insert_seat(
                    conn, match_id, seat, kind, session_key if kind == "human" else None
                )
            await self._seated(match_id, seat)

    async def _seated(self, match_id: str, seat: str) -> None:
        """A seat was filled; the table starts when it is full. Runs under the match lock."""
        _, rec = await self._load(match_id)
        self.bus.emit(match_id, "seat_joined", SeatJoined(seat=seat, state_version=0))
        if len(rec.seats) >= rec.seats_wanted:
            async with self.pool.connection() as conn:
                await conn.execute(
                    "update matches set status = 'active', updated_at = now() "
                    "where id = %s and status = 'open'",
                    (match_id,),
                )
            match, rec = await self._load(match_id)
            template = self.template_of(match)
            self.bus.emit(match_id, "match_started", MatchStarted(state_version=0))
            if template.mode == "showcase":
                await self._set_clock(match, rec, template)
                self._start_model_answers(match, rec)
            else:
                await self._after_turn(match, template, rec)
        self._spawn(self._broadcast_lobby())

    async def open_tables(self) -> list[TableView]:
        async with self.pool.connection() as conn:
            rows = await (
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
        return [
            TableView(
                id=r["id"],
                template_id=r["template_id"],
                title=self.templates[r["template_id"]].title,
                emblem=self.templates[r["template_id"]].emblem,
                host_name=r["host_name"] or "Challenger",
                invite_code=r["invite_code"],
                seats_taken=r["taken"],
                seats_wanted=r["seats_wanted"],
                created_at=r["created_at"].isoformat(),
            )
            for r in rows
            if r["template_id"] in self.templates
        ]

    async def _broadcast_lobby(self) -> None:
        await self.presence.broadcast(Lobby(tables=await self.open_tables()))

    async def set_visibility(self, match_id: str, session_key: str, public: bool) -> None:
        _, rec = await self._load(match_id)
        await self._check_owner(rec, session_key)
        async with self.pool.connection() as conn:
            await conn.execute(
                "update matches set is_public = %s, is_curated = is_curated and %s where id = %s",
                (public, public, match_id),
            )

    # Loading and saving

    async def _load(self, match_id: str) -> tuple[Match, Record]:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("select * from matches where id = %s", (match_id,))
            ).fetchone()
            if row is None:
                raise MatchError(404, "no such match")
            turns = await (
                await conn.execute(
                    "select t.*, v.scoring, v.host from turns t "
                    "left join verdicts v on v.id = t.live_verdict_id "
                    "where t.match_id = %s and t.seq is not null order by t.seq",
                    (match_id,),
                )
            ).fetchall()
            guesses = await (
                await conn.execute(
                    "select round_n, actor, picked, points, awarded_to from guesses "
                    "where match_id = %s order by id",
                    (match_id,),
                )
            ).fetchall()
            seat_rows = await (
                await conn.execute(
                    "select se.*, s.stage_name, s.account_id from seats se "
                    "left join sessions s on s.session_key = se.session_key "
                    "where se.match_id = %s order by substring(se.seat from 2)::int",
                    (match_id,),
                )
            ).fetchall()
        if match_id not in self.match_templates and row["config"]:
            try:
                self.match_templates[match_id] = Template.model_validate(row["config"])
            except ValidationError:
                log.warning("match %s stored a template this build cannot read", match_id)
        match = Match(
            id=row["id"],
            template_id=row["template_id"],
            template_version=row["template_version"],
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
                )
                for t in turns
            ],
            guesses=[Guess(**g) for g in guesses],
            guessers=tuple(r["seat"] for r in seat_rows if r["kind"] == "human"),
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

    async def _save(self, match: Match, conn: Any = None) -> None:
        """Writes the match state. A match already closed in the database stays closed:
        the write is refused with MatchClosed."""
        if conn is None:
            async with self.pool.connection() as own, own.transaction():
                await self._save(match, own)
            return
        result = await conn.execute(
            "update matches set status = %s, state_version = %s, to_move = %s, phase = %s, "
            "round_n = %s, winner = %s, end_reason = %s, updated_at = now(), "
            "turn_deadline = case when %s in ('ended', 'abandoned') then null "
            "else turn_deadline end, "
            "ended_at = case when %s = 'ended' "
            "and ended_at is null then now() else ended_at end "
            "where id = %s and status not in ('ended', 'abandoned')",
            (
                match.status,
                match.state_version,
                match.to_move,
                match.phase,
                match.round_n,
                match.winner,
                match.end_reason,
                match.status,
                match.status,
                match.id,
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

    async def _store_turn(
        self,
        match: Match,
        actor: Actor,
        move_text: str,
        outcome: Outcome,
        seq: int | None,
        layer1_result: str | None,
        verdict_id: int | None,
        action_id: str | None,
    ) -> None:
        """One transaction: the new match state and the turn that produced it."""
        async with self.pool.connection() as conn, conn.transaction():
            await self._save(match, conn)
            await self._insert_turn(
                conn, match, actor, move_text, outcome, seq, layer1_result, verdict_id, action_id
            )

    async def _set_status(self, match_id: str, status: str) -> None:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                "update matches set status = %s, updated_at = now() "
                "where id = %s and status not in ('ended', 'abandoned')",
                (status, match_id),
            )
        if result.rowcount == 0:
            raise MatchClosed(match_id)

    async def _set_submitted(self, match_id: str, seat: str, submitted: bool) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update seats set submitted_at = case when %s then now() end "
                "where match_id = %s and seat = %s",
                (submitted, match_id, seat),
            )

    async def _set_clock(
        self, match: Match, rec: Record, template: Template, running: bool = True
    ) -> str | None:
        """Starts the clock for the turn or phase in play when two or more humans share the
        table; clears it otherwise, or when not running."""
        clocked = running and rec.clocked
        deadline = datetime.now(UTC) + self._clock_length(match, template) if clocked else None
        if deadline is None and rec.turn_deadline is None:
            return None
        async with self.pool.connection() as conn:
            await conn.execute(
                "update matches set turn_deadline = %s where id = %s", (deadline, match.id)
            )
        return deadline.isoformat() if deadline else None

    async def close_abandoned(self) -> list[str]:
        """Closes every match idle past the abandon window and every table still unfilled
        past its window. Returns their ids."""
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "update matches set status = 'abandoned', end_reason = 'abandoned', "
                    "turn_deadline = null, ended_at = now(), updated_at = now() "
                    "where status in ('active', 'awaiting_judgment', 'paused') "
                    "and updated_at < now() - %s returning id",
                    (ABANDON_WINDOW,),
                )
            ).fetchall()
            stale = await (
                await conn.execute(
                    "select id from matches where status = 'open' and created_at < now() - %s",
                    (UNFILLED_WINDOW,),
                )
            ).fetchall()
        unfilled = []
        for row in stale:
            # Under the match lock, so a join already under way either lands first or finds
            # the table closed.
            async with self.locks[row["id"]]:
                async with self.pool.connection() as conn:
                    closed = await conn.execute(
                        "update matches set status = 'abandoned', end_reason = 'unfilled', "
                        "ended_at = now(), updated_at = now() where id = %s and status = 'open'",
                        (row["id"],),
                    )
                if closed.rowcount == 0:
                    continue
                match, _ = await self._load(row["id"])
                await self._emit_match_ended(match, coaching_line=None)
                unfilled.append(row["id"])
        for row in rows:
            self._forget(row["id"])
        if unfilled:
            await self._broadcast_lobby()
        return [*(row["id"] for row in rows), *unfilled]

    async def check_exists(self, match_id: str) -> None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("select 1 from matches where id = %s", (match_id,))
            ).fetchone()
        if row is None:
            raise MatchError(404, "no such match")

    async def _status_of(self, match_id: str) -> str:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("select status from matches where id = %s", (match_id,))
            ).fetchone()
        return row["status"] if row else "abandoned"

    async def _insert_turn(
        self,
        conn: Any,
        match: Match,
        actor: Actor,
        move_text: str,
        outcome: Outcome,
        seq: int | None,
        layer1_result: str | None,
        verdict_id: int | None,
        action_id: str | None,
    ) -> int:
        round_n = match.turns[-1].round_n if seq is not None and match.turns else match.round_n
        row = await (
            await conn.execute(
                "insert into turns (match_id, seq, actor, move_text, layer1_result, outcome, "
                "live_verdict_id, action_id, round_n, model_ref) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "(select model_ref from seats where match_id = %s and seat = %s)) returning id",
                (
                    match.id,
                    seq,
                    actor,
                    move_text,
                    layer1_result,
                    outcome,
                    verdict_id,
                    action_id,
                    round_n,
                    match.id,
                    actor,
                ),
            )
        ).fetchone()
        assert row is not None
        if verdict_id is not None:
            await conn.execute(
                "update verdicts set turn_id = %s where id = %s", (row["id"], verdict_id)
            )
        return row["id"]

    async def _insert_guess(
        self, conn: Any, match: Match, guess: Guess, action_id: str | None
    ) -> None:
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

    async def _stamp_badges(self, verdict_id: int, badges: list[str]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update verdicts set host = host || jsonb_build_object('badges', %s::jsonb) "
                "where id = %s",
                (json.dumps(badges), verdict_id),
            )

    async def _insert_verdict(self, call: JudgeCall) -> int:
        response = call.response
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "insert into verdicts (judge_model, prompt_hash, raw_response, scoring, host, "
                    "latency_ms, tokens_in, tokens_out, cost_usd) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
                    (
                        self.judge_model,
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

    # Snapshots

    @staticmethod
    def _display_name(row: SeatRow, rec: Record) -> str:
        if row.kind == "human":
            return row.stage_name or "Challenger"
        houses = sorted(rec.models, key=lambda seat: int(seat[1:]))
        n = houses.index(row.seat) + 1
        return "The House" if n == 1 else f"The House {n}"

    def _hides_round(self, match: Match, template: Template) -> bool:
        """Showcase: the round in play stays off the wire until it is revealed."""
        return template.mode == "showcase" and match.status not in ("ended", "abandoned")

    def _shown_points(self, match: Match, template: Template, rec: Record) -> dict[str, int]:
        """Seat totals as of the last revealed round: the round in play is still secret."""
        shown = dict(match.points)
        if not self._hides_round(match, template):
            return shown
        for t in rec.turn_rows:
            if t["round_n"] == match.round_n:
                shown[t["actor"]] -= self._turn_points(t["scoring"], template, t["outcome"]) or 0
        for g in match.round_guesses(match.round_n):
            shown[g.awarded_to] -= g.points
        return shown

    def _in_call(self, match: Match, round_n: int) -> bool:
        return match.phase == "guess" and round_n == match.round_n

    def _rounds(self, match: Match, template: Template, viewer: str | None) -> list[RoundView]:
        if template.mode == "escalation":
            return []
        over = match.status in ("ended", "abandoned")
        owes = viewer is not None and viewer in match.owed_guesses()
        views = []
        for n, token in enumerate(match.cards[: match.round_n], start=1):
            card = template.seed_named(token)
            assert card is not None
            in_call = self._in_call(match, n)
            revealed = (n < match.round_n or over) and not in_call
            views.append(
                RoundView(
                    round_n=n,
                    token=token,
                    emoji=card.opening_emoji if revealed else "",
                    detail=card.detail,
                    truth=card.hidden if revealed else None,
                    options=self._options(match, card, viewer)
                    if in_call and owes and viewer
                    else [],
                    guesses=self._guess_views(match, n) if revealed else [],
                )
            )
        return views

    @staticmethod
    def _table(match: Match, card: Seed, guesser: Actor) -> dict[str, tuple[str, str]]:
        """The entries on the table for a guesser, keyed by a hash that says nothing about them."""
        bluffs = {t.actor: t.move_text for t in match.round_turns(match.round_n)}
        table = {}
        for pick in match.guess_options(guesser):
            text = entry_case(card.hidden if pick == "truth" else bluffs[pick])
            key = hashlib.sha256(f"{match.id}:{match.round_n}:{text}".encode()).hexdigest()[:8]
            table[key] = (pick, text)
        return table

    def _options(self, match: Match, card: Seed, guesser: Actor) -> list[GuessOption]:
        table = self._table(match, card, guesser)
        return [GuessOption(key=key, text=table[key][1]) for key in sorted(table)]

    def _resolve_pick(self, match: Match, card: Seed, guesser: Actor, key: str) -> str:
        entry = self._table(match, card, guesser).get(key)
        if entry is None:
            raise MatchError(422, "that entry is not on the table")
        return entry[0]

    @staticmethod
    def _guess_views(match: Match, round_n: int) -> list[GuessView]:
        return [
            GuessView(actor=g.actor, picked=g.picked, points=g.points, awarded_to=g.awarded_to)
            for g in match.round_guesses(round_n)
        ]

    def _seat_views(self, match: Match, template: Template, rec: Record) -> list[SeatView]:
        shown = self._shown_points(match, template, rec)
        if match.phase == "guess":
            # A seat that owes no call (a model seat, or a guesser with no choice) is done.
            answered = set(match.seats) - set(match.owed_guesses())
        else:
            answered = {t.actor for t in match.turns if t.round_n == match.round_n}
        return [
            SeatView(
                seat=row.seat,
                kind=row.kind,
                display_name=self._display_name(row, rec),
                model=self.opponent_name if row.kind == "model" else None,
                points=shown[row.seat],
                eliminated=row.seat in match.eliminated,
                answered=template.mode == "showcase" and (row.seat in answered or row.submitted),
            )
            for row in rec.seats
        ]

    async def snapshot(self, match_id: str, session_key: str | None = None) -> MatchSnapshot:
        match, rec = await self._load(match_id)
        template = self.template_of(match)
        viewer = await self._seat_of(rec, session_key)
        if viewer and template.mode == "showcase" and match.status == "active":
            self._start_model_answers(match, rec)
        hide = self._hides_round(match, template)
        returned = await self._returned(match, template, rec, viewer) if hide and viewer else None
        rows = [
            t
            for t in rec.turn_rows
            # The round in play shows only the viewer's own answer, and none while it is called.
            if not (
                hide
                and t["round_n"] == match.round_n
                and (match.phase == "guess" or t["actor"] != viewer)
            )
        ]
        return MatchSnapshot(
            id=match.id,
            template_id=match.template_id,
            title=template.title,
            mode=template.mode,
            kind=rec.kind,
            status=match.status,
            state_version=match.state_version,
            phase=match.phase,
            seed_token=match.seed,
            seed_emoji=match.seed_emoji if template.mode == "escalation" else "",
            rounds=self._rounds(match, template, viewer),
            round_in_play=match.round_n,
            seats=self._seat_views(match, template, rec),
            seats_wanted=rec.seats_wanted,
            your_seat=viewer,
            invite_code=rec.invite_code if viewer and match.status == "open" else None,
            closes_at=(rec.created_at + UNFILLED_WINDOW).isoformat()
            if match.status == "open"
            else None,
            turn_deadline=rec.turn_deadline.isoformat() if rec.turn_deadline else None,
            clock_seconds=int(self._clock_length(match, template).total_seconds())
            if rec.turn_deadline
            else None,
            to_move=match.to_move,
            winner=match.winner,
            end_reason=match.end_reason,
            judged_moves=match.judged_moves,
            transcript=[
                TurnView(
                    seq=t["seq"],
                    round_n=t["round_n"],
                    actor=t["actor"],
                    move_text=t["move_text"],
                    outcome=t["outcome"],
                    scoring=t["scoring"],
                    host=t["host"],
                    points=self._turn_points(t["scoring"], template, t["outcome"]),
                    played_by=self._stand_in(t["model_ref"]),
                )
                for t in rows
            ],
            returned=returned,
            created_at=rec.created_at.isoformat(),
            is_public=rec.is_public,
            is_yours=viewer is not None,
        )

    async def _returned(
        self, match: Match, template: Template, rec: Record, viewer: str
    ) -> TurnRejected | None:
        """Showcase: why the viewer's last answer this round came back, while it still owes one."""
        if match.phase != "write" or viewer in match.eliminated or match.has_answered(viewer):
            return None
        if rec.row(viewer).submitted:
            return None
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "select t.outcome, t.layer1_result, v.host from turns t "
                    "left join verdicts v on v.id = t.live_verdict_id "
                    "where t.match_id = %s and t.actor = %s and t.round_n = %s "
                    "and t.seq is null order by t.id desc limit 1",
                    (match.id, viewer, match.round_n),
                )
            ).fetchone()
        if row is None:
            return None
        if row["outcome"] == "semantic_reject" and row["host"]:
            host = HostPayload.model_validate(row["host"])
            return self._rejection(
                match, template, viewer, "semantic_reject", host.headline, host.quotable_line
            )
        reason = row["layer1_result"]
        if reason == "repeat":
            text = REPEAT_TEXT
        elif reason in ("empty", "too_long", "duplicate"):
            text = getattr(template.validation_messages, reason)
        else:
            return None
        return self._rejection(match, template, viewer, "deterministic_invalid", text)

    @staticmethod
    def _clock_length(match: Match, template: Template) -> timedelta:
        if template.mode == "escalation":
            return TURN_CLOCK
        return CALL_CLOCK if match.phase == "guess" else WRITE_CLOCK

    async def replay(self, match_id: str, session_key: str | None = None) -> Replay:
        _, rec = await self._load(match_id)
        snap = await self.snapshot(match_id, session_key)
        if snap.status not in ("ended", "abandoned"):
            raise MatchError(404, "match still running")
        return Replay(
            **snap.model_dump(),
            share_text=self._share_text(snap),
            highlight_seq=self._highlight_seq(snap),
            is_curated=rec.is_curated,
        )

    def _turn_points(
        self, scoring: dict[str, Any] | None, template: Template, outcome: str
    ) -> int | None:
        """What the move was awarded. A move that fell takes nothing, whatever it scored."""
        if scoring is None:
            return None
        if outcome == "fail":
            return 0
        return weighted_total(scoring["scores"], template.weights)

    def _highlight_seq(self, snap: MatchSnapshot) -> int | None:
        weights = self.templates[snap.template_id].weights
        winner_moves = [
            t
            for t in snap.transcript
            if t.actor == snap.winner and t.scoring and t.outcome != "fail"
        ]
        best = max(
            winner_moves,
            key=lambda t: weighted_total(t.scoring.scores, weights),  # type: ignore[union-attr]
            default=None,
        )
        return best.seq if best else None

    async def stage(self) -> StageView:
        async with self.pool.connection() as conn:
            live = await (
                await conn.execute(
                    "select id from matches where is_public and status in "
                    "('active', 'awaiting_judgment', 'paused') "
                    "and created_at > now() - interval '2 hours' "
                    "order by created_at desc limit 6"
                )
            ).fetchall()
            played = await (
                await conn.execute("select count(*) as n from matches where status = 'ended'")
            ).fetchone()
        return StageView(
            live=[await self.snapshot(r["id"]) for r in live],
            duels_played=played["n"] if played else 0,
        )

    async def replays(self, sort: Literal["curated", "newest", "longest"]) -> list[Replay]:
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
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"select id from matches m where {where} order by {order} limit 12"
                )
            ).fetchall()
        return [await self.replay(r["id"]) for r in rows]

    async def set_curated(self, match_id: str, curated: bool) -> None:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                "update matches set is_curated = %s, is_public = is_public or %s "
                "where id = %s and status = 'ended'",
                (curated, curated, match_id),
            )
            if result.rowcount == 0:
                raise MatchError(404, "no finished match with that id")

    def _share_text(self, snap: MatchSnapshot) -> str:
        """Written from the seat of the table's creator."""
        link = f"{self.public_base_url}/r/{snap.id}"
        owner = next(s for s in snap.seats if s.kind == "human")
        result = "won" if snap.winner == owner.seat else "lost" if snap.winner else "drew"
        game = "a duel" if len(snap.seats) == 2 else f"a {len(snap.seats)}-player game"
        if snap.mode == "showcase":
            best_other = max((s.points for s in snap.seats if s.seat != owner.seat), default=0)
            cards = " ".join(r.emoji for r in snap.rounds if r.emoji)
            return (
                f"I {result} {game} of {snap.title}, {owner.points} to {best_other}. {cards} {link}"
            )
        chain = [snap.seed_emoji] + [
            t.host.generated_emoji for t in snap.transcript if t.host and t.outcome in STANDING
        ]
        return (
            f"I {result} {game} of {snap.title} in {snap.judged_moves} moves. "
            f"{'→'.join(chain)} {link}"
        )

    # Commands

    async def submit_move(
        self,
        match_id: str,
        session_key: str,
        action_id: str,
        expected_version: int,
        move_text: str,
        round_n: int | None = None,
    ) -> None:
        async with self.locks[match_id]:
            if await self._action_seen(match_id, action_id):
                return
            match, rec = await self._load(match_id)
            seat = await self._check_command(
                match, rec, session_key, "move", expected_version, round_n
            )
            template = self.template_of(match)
            if template.mode == "showcase":
                self._start_model_answers(match, rec)
                await self._set_submitted(match_id, seat, True)
                self.bus.emit(
                    match_id,
                    "seat_submitted",
                    SeatSubmitted(seat=seat, state_version=match.state_version),
                )
                self._spawn(self._answer_as_human(match_id, seat, action_id, move_text))
                return
            await self._set_status(match_id, "awaiting_judgment")
        self._spawn(self._run_human_move(match_id, seat, action_id, move_text))

    async def submit_guess(
        self,
        match_id: str,
        session_key: str,
        action_id: str,
        expected_version: int,
        key: str,
        round_n: int | None = None,
    ) -> None:
        """A guesser calls the real entry. No judge is involved; the last call reveals the round."""
        async with self.locks[match_id]:
            if await self._action_seen(match_id, action_id):
                return
            match, rec = await self._load(match_id)
            seat = await self._check_command(
                match, rec, session_key, "guess", expected_version, round_n
            )
            template = self.template_of(match)
            card = template.seed_named(match.card)
            assert card is not None
            pick = self._resolve_pick(match, card, seat, key)
            try:
                apply_guess(match, seat, pick, match.state_version, template)
            except (StaleVersionError, ValueError) as e:
                raise MatchError(409, str(e)) from e
            async with self.pool.connection() as conn, conn.transaction():
                await self._save(match, conn)
                await self._insert_guess(conn, match, match.guesses[-1], action_id)
            self.bus.emit(
                match_id,
                "seat_submitted",
                SeatSubmitted(seat=seat, state_version=match.state_version),
            )
            if match.phase == "write":
                await self._round_closed(match, template, rec)

    async def resign_match(
        self, match_id: str, session_key: str, action_id: str, expected_version: int
    ) -> None:
        async with self.locks[match_id]:
            match, rec = await self._load(match_id)
            if await self._action_seen(match_id, action_id):
                return
            seat = await self._check_command(match, rec, session_key, "resign", expected_version)
            template = self.template_of(match)
            before = (match.round_n, match.phase)
            resign(match, seat, match.state_version, template)
            await self._store_turn(
                match, seat, "", "deterministic_invalid", None, "resign", None, action_id
            )
            if await self._emit_if_ended(match):
                return
            if template.mode == "showcase":
                if (match.round_n, match.phase) != before:
                    await self._round_closed(match, template, rec)
                return
            await self._after_turn(match, template, rec)

    async def disagree(self, match_id: str, seq: int, session_key: str | None) -> None:
        """One vote per seated session and move, on a move the table can already see."""
        match, rec = await self._load(match_id)
        template = self.template_of(match)
        if session_key is None or await self._seat_of(rec, session_key) is None:
            raise MatchError(403, "only a seat at this table can disagree")
        turn = next((t for t in match.turns if t.seq == seq), None)
        if turn is None or (self._hides_round(match, template) and turn.round_n >= match.round_n):
            raise MatchError(404, "no such turn")
        if template.mode == "showcase":
            previous = match.cards[turn.round_n - 1]
        else:
            previous = next(
                (t.move_text for t in reversed(match.turns[: seq - 1]) if t.outcome in JUDGED),
                match.seed,
            )
        async with self.pool.connection() as conn, conn.transaction():
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
                (match.template_id, normalize(previous), normalize(turn.move_text)),
            )

    async def _seat_of(self, rec: Record, session_key: str | None) -> str | None:
        """The seat held by the session, or by any session signed in to the same account."""
        if session_key is None:
            return None
        for row in rec.humans:
            if row.session_key == session_key:
                return row.seat
        if not any(row.account_id for row in rec.humans):
            return None
        async with self.pool.connection() as conn:
            account = await account_of(conn, session_key)
        return next((r.seat for r in rec.humans if account and r.account_id == account), None)

    async def _check_owner(self, rec: Record, session_key: str) -> None:
        if (await self._seat_of(rec, session_key)) != rec.owner.seat:
            raise MatchError(403, "not your match")

    async def _check_command(
        self,
        match: Match,
        rec: Record,
        session_key: str,
        action: Literal["move", "guess", "resign"],
        expected_version: int,
        round_n: int | None = None,
    ) -> str:
        """The requester's seat, when it may act now. Escalation moves carry the state
        version; showcase answers and calls are per seat and carry the round they were for."""
        seat = await self._seat_of(rec, session_key)
        if seat is None:
            raise MatchError(403, "not your match")
        if match.status in ("ended", "abandoned"):
            raise MatchError(409, "match already ended")
        if match.status == "open":
            raise MatchError(409, "the table is still filling")
        if seat in match.eliminated:
            raise MatchError(409, "you are out of this match")
        if action == "resign":
            return seat
        template = self.template_of(match)
        if template.mode == "showcase" and round_n is not None and round_n != match.round_n:
            raise MatchError(409, "that round is over")
        if action == "guess":
            if match.phase != "guess" or seat not in match.owed_guesses():
                raise MatchError(409, "no call to make")
            return seat
        if match.phase != "write":
            raise MatchError(409, "not your move")
        if template.mode == "showcase":
            if match.has_answered(seat) or rec.row(seat).submitted:
                raise MatchError(409, "you already answered this round")
            return seat
        if match.status != "active" or match.to_move != seat:
            raise MatchError(409, "not your move")
        if expected_version != match.state_version:
            raise MatchError(409, f"stale version: match is at {match.state_version}")
        return seat

    async def _action_seen(self, match_id: str, action_id: str) -> bool:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "select 1 from turns where match_id = %s and action_id = %s "
                    "union all select 1 from guesses where match_id = %s and action_id = %s",
                    (match_id, action_id, match_id, action_id),
                )
            ).fetchone()
        return row is not None

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background task failed", exc_info=task.exception())

    # Escalation turns

    async def _run_human_move(
        self, match_id: str, seat: str, action_id: str, move_text: str
    ) -> None:
        async with self.locks[match_id]:
            match, rec = await self._load(match_id)
            if match.status != "awaiting_judgment" or match.to_move != seat:
                return
            template = self.template_of(match)
            try:
                ended = await self._play_move(match, template, seat, move_text, action_id, rec)
                if not ended:
                    await self._after_turn(match, template, rec)
            except MatchClosed:
                log.info("match %s closed while waiting on the judge", match_id)
            except Exception:
                log.exception("move on %s failed; the match goes back to a human seat", match_id)
                await self._set_status(match_id, "active")
                match, rec = await self._load(match_id)
                if match.to_move in rec.models:
                    await self._forfeit(match, template, match.to_move)
                    if not await self._emit_if_ended(match):
                        await self._after_turn(match, template, rec)
                    return
                await self._give_time(match, rec, template)
                match, rec = await self._load(match_id)
                self.bus.emit(
                    match_id,
                    "turn_changed",
                    TurnChanged(
                        to_move=match.to_move,
                        turn_deadline=rec.turn_deadline.isoformat() if rec.turn_deadline else None,
                        round_in_play=match.round_n,
                        state_version=match.state_version,
                    ),
                )

    async def _after_turn(self, match: Match, template: Template, rec: Record) -> None:
        """Escalation, under the match lock: model seats play until a human seat is to move,
        whose clock then starts."""
        if match.status == "active" and match.to_move in rec.models:
            await self._play_opponent(match, template, rec)
        if match.status != "active":
            return
        deadline = await self._set_clock(match, rec, template)
        self.bus.emit(
            match.id,
            "turn_changed",
            TurnChanged(
                to_move=match.to_move,
                turn_deadline=deadline,
                round_in_play=match.round_n,
                state_version=match.state_version,
            ),
        )
        mover = rec.row(match.to_move)
        if rec.clocked and mover.session_key:
            await self.presence.send(
                mover.session_key, TurnNudge(match_id=match.id, title=template.title)
            )

    async def _refuse_layer1(
        self, match: Match, template: Template, actor: Actor, move_text: str, action_id: str | None
    ) -> bool:
        """Applies a Layer 1 refusal when there is one. Returns True when the move was refused."""
        reason = layer1(template, move_text, match)
        if reason is None:
            return False
        apply_ruling(
            match, actor, move_text, "deterministic_invalid", match.state_version, template
        )
        await self._store_turn(
            match, actor, move_text, "deterministic_invalid", None, reason, None, action_id
        )
        self._emit_rejection(
            match,
            template,
            self._rejection(
                match,
                template,
                actor,
                "deterministic_invalid",
                getattr(template.validation_messages, reason),
            ),
        )
        return True

    async def _refuse_semantic(
        self,
        match: Match,
        template: Template,
        actor: Actor,
        move_text: str,
        judged: Judged,
        action_id: str | None,
    ) -> None:
        apply_ruling(match, actor, move_text, "semantic_reject", match.state_version, template)
        await self._store_turn(
            match, actor, move_text, "semantic_reject", None, None, judged.verdict_id, action_id
        )
        host = judged.response.host
        self._emit_rejection(
            match,
            template,
            self._rejection(
                match, template, actor, "semantic_reject", host.headline, host.quotable_line
            ),
        )

    async def _strike_out(self, match: Match, template: Template, seat: str, rec: Record) -> bool:
        """At a table of humans a seat that runs out of strikes loses the turn. Returns True
        when it did; a House duel keeps its strikes as nudges only."""
        if not rec.clocked or seat in rec.models:
            return False
        if match.strikes[seat] < template.strikes_before_consequence:
            return False
        await self._forfeit(match, template, seat)
        return True

    async def _forfeit(self, match: Match, template: Template, seat: str) -> None:
        forfeit_turn(match, seat, template)
        await self._store_turn(match, seat, "", "forfeit", len(match.turns), "forfeit", None, None)

    async def _record_ruling(
        self,
        match: Match,
        template: Template,
        actor: Actor,
        move_text: str,
        judged: Judged,
        seq: int,
        action_id: str | None,
        hidden: str = "",
    ) -> Ruling:
        """Applies and stores a ruling. Returns the event; the caller decides when it goes out."""
        response = judged.response
        before = match.points[actor]
        earned = weighted_total(response.scoring.scores, template.weights)
        proximity = response.scoring.truth_proximity if hidden else "none"
        apply_ruling(
            match,
            actor,
            move_text,
            judged.outcome,
            match.state_version,
            template,
            earned,
            truth_hit=proximity == "hit",
        )
        await self._store_turn(
            match, actor, move_text, judged.outcome, seq, None, judged.verdict_id, action_id
        )
        badges = ["close_call"] if judged.outcome == "semantic_uncertain" else []
        if judged.outcome != "fail" and proximity == "hit":
            badges.append("accidental_truth")
        elif judged.outcome != "fail" and proximity == "near":
            badges.append("near_miss")
        if badges:
            await self._stamp_badges(judged.verdict_id, badges)
        return Ruling(
            seq=seq,
            round_n=match.turns[-1].round_n,
            actor=actor,
            move_text=move_text,
            outcome=judged.outcome,
            scoring=response.scoring,
            host=response.host.model_copy(update={"badges": badges}),
            points=match.points[actor] - before,
            totals=dict(match.points),
            to_move=match.to_move,
            round_in_play=match.round_n,
            state_version=match.state_version,
        )

    async def _play_move(
        self,
        match: Match,
        template: Template,
        actor: Actor,
        move_text: str,
        action_id: str | None,
        rec: Record,
    ) -> bool:
        """Escalation: one move through Layer 1 and the judge. Returns True when the match ended."""
        if await self._refuse_layer1(match, template, actor, move_text, action_id):
            await self._strike_out(match, template, actor, rec)
            return await self._emit_if_ended(match)
        seq = len(match.turns) + 1
        self.bus.emit(match.id, "judge_started", JudgeStarted(seq=seq))
        judged = await self._judge_until_ruled(
            match, template, seq, move_text, match.standing_form, transcript(match, template)
        )
        if judged.outcome == "semantic_reject":
            await self._refuse_semantic(match, template, actor, move_text, judged, action_id)
            await self._strike_out(match, template, actor, rec)
            return await self._emit_if_ended(match)
        ruling = await self._record_ruling(
            match, template, actor, move_text, judged, seq, action_id
        )
        self.bus.emit(match.id, "ruling", ruling)
        if match.status == "ended":
            coaching = judged.response.host.coaching_line if judged.outcome == "fail" else None
            await self._emit_match_ended(match, coaching)
            return True
        return False

    async def _emit_if_ended(self, match: Match) -> bool:
        if match.status == "ended":
            await self._emit_match_ended(match, coaching_line=None)
            return True
        return False

    async def _play_opponent(self, match: Match, template: Template, rec: Record) -> None:
        """Plays every model seat whose turn it is, until a human seat is to move."""
        while match.status == "active" and match.to_move in rec.models:
            seat = match.to_move
            strikes_before = match.strikes[seat]
            while match.status == "active" and match.to_move == seat:
                refusals = match.strikes[seat] - strikes_before
                if refusals < template.strikes_before_consequence:
                    move_text = await self._stream_opponent_move(match, template, seat, match.seed)
                elif refusals == template.strikes_before_consequence:
                    move_text = template.default_move
                else:
                    resign(match, seat, match.state_version, template)
                    await self._save(match)
                    if match.status == "ended":
                        await self._emit_match_ended(match, coaching_line=None)
                    break
                if await self._play_move(match, template, seat, move_text, None, rec):
                    return

    # Showcase turns

    def _start_model_answers(self, match: Match, rec: Record) -> None:
        """Every model seat starts writing and being judged for the round in play."""
        if match.status != "active" or match.phase != "write":
            return
        for seat in rec.models:
            if seat in match.eliminated:
                continue
            if match.has_answered(seat):
                continue
            key = (match.id, seat, match.round_n)
            if key in self.answering:
                continue
            self.answering.add(key)
            self._spawn(self._answer_as_model(match.id, seat, match.round_n))

    async def _answer_as_model(
        self, match_id: str, seat: str, round_n: int, tries: int = 0
    ) -> None:
        rewrite = False
        try:
            match, rec = await self._load(match_id)
            if not self._still_owed(match, seat, round_n):
                return
            template = self.template_of(match)
            card = template.seed_named(match.card)
            assert card is not None
            lines = transcript(match, template, finished_only=True)
            row = rec.row(seat)
            held = row.held_move if row.held_round == round_n else None
            try:
                judged, text = await self._write_and_judge_model(
                    match, template, seat, held, card, lines
                )
            except (HouseStuck, JudgeGaveUp):
                async with self.locks[match_id]:
                    match, rec = await self._load(match_id)
                    if match.status != "active" or match.round_n != round_n:
                        return
                    resign(match, seat, match.state_version, template)
                    await self._save(match)
                    await self._after_showcase_change(match, template, rec, round_n)
                return
            async with self.locks[match_id]:
                match, rec = await self._load(match_id)
                if not self._still_owed(match, seat, round_n):
                    return
                await self._hold(match_id, seat, None, round_n)
                # Two identical entries could not be told apart on the call.
                if repeats_the_round(match, text):
                    if tries < MODEL_REWRITES:
                        rewrite = True
                        return
                    resign(match, seat, match.state_version, template)
                    await self._save(match)
                    await self._after_showcase_change(match, template, rec, round_n)
                    return
                await self._record_ruling(
                    match, template, seat, text, judged, len(match.turns) + 1, None, card.hidden
                )
                await self._after_showcase_change(match, template, rec, round_n)
        except MatchClosed:
            log.info("match %s closed while a model seat was writing", match_id)
        finally:
            self.answering.discard((match_id, seat, round_n))
            if rewrite:
                self.answering.add((match_id, seat, round_n))
                self._spawn(self._answer_as_model(match_id, seat, round_n, tries + 1))

    async def _answer_as_human(
        self, match_id: str, seat: str, action_id: str, move_text: str
    ) -> None:
        """Showcase: judges one seat's answer without holding up the other seats. Nothing about
        it leaves the server until the round is revealed."""
        try:
            match, rec = await self._load(match_id)
            template = self.template_of(match)
            round_n = match.round_n
            card = template.seed_named(match.card)
            assert card is not None
            reason = layer1(template, move_text, match)
            judged = None
            if reason is None:
                judged = await self._judge_until_ruled(
                    match,
                    template,
                    len(match.turns) + 1,
                    move_text,
                    card.card_text,
                    transcript(match, template, finished_only=True),
                    card.hidden,
                    quiet=True,
                )
            async with self.locks[match_id]:
                await self._set_submitted(match_id, seat, False)
                match, rec = await self._load(match_id)
                if not self._still_owed(match, seat, round_n):
                    return
                if judged is None:
                    await self._refuse_layer1(match, template, seat, move_text, action_id)
                elif judged.outcome == "semantic_reject":
                    await self._refuse_semantic(match, template, seat, move_text, judged, action_id)
                elif repeats_the_round(match, move_text):
                    # No strike: the seat could not have known.
                    await self._store_turn(
                        match,
                        seat,
                        move_text,
                        "deterministic_invalid",
                        None,
                        "repeat",
                        None,
                        action_id,
                    )
                    self._emit_rejection(
                        match,
                        template,
                        self._rejection(
                            match, template, seat, "deterministic_invalid", REPEAT_TEXT
                        ),
                    )
                    await self._give_time(match, rec, template)
                    return
                else:
                    await self._record_ruling(
                        match,
                        template,
                        seat,
                        move_text,
                        judged,
                        len(match.turns) + 1,
                        action_id,
                        card.hidden,
                    )
                    self.bus.emit(
                        match_id,
                        "seat_submitted",
                        SeatSubmitted(seat=seat, state_version=match.state_version),
                    )
                    await self._after_showcase_change(match, template, rec, round_n)
                    return
                if await self._strike_out(match, template, seat, rec):
                    await self._after_showcase_change(match, template, rec, round_n)
                else:
                    await self._give_time(match, rec, template)
        except MatchClosed:
            log.info("match %s closed while waiting on the judge", match_id)
        except Exception:
            log.exception("answer on %s failed; the seat may answer again", match_id)
            async with self.locks[match_id]:
                await self._set_submitted(match_id, seat, False)
                match, rec = await self._load(match_id)
                await self._give_time(match, rec, self.template_of(match))

    async def _give_time(self, match: Match, rec: Record, template: Template) -> None:
        """A seat handed back its move after the judge ran long gets a whole clock again."""
        if not rec.clocked or match.status not in ("active", "awaiting_judgment"):
            return
        left = rec.turn_deadline - datetime.now(UTC) if rec.turn_deadline else None
        if left is None or left < GRACE:
            await self._set_clock(match, rec, template)

    def _still_owed(self, match: Match, seat: str, round_n: int) -> bool:
        """The seat still owes an answer for the round it wrote for."""
        return (
            match.status == "active"
            and match.phase == "write"
            and match.round_n == round_n
            and seat not in match.eliminated
            and not match.has_answered(seat)
        )

    async def _after_showcase_change(
        self, match: Match, template: Template, rec: Record, round_n: int
    ) -> None:
        """After a showcase round moved: a finished round reveals (and ends the match when it
        was the last), a match ended mid-round says so, a round now being called opens."""
        if match.round_n != round_n and match.phase == "write":
            await self._round_closed(match, template, rec)
        elif match.status == "ended":
            await self._emit_match_ended(match, coaching_line=None)
        elif match.phase == "guess":
            await self._round_closed(match, template, rec)

    async def _round_closed(self, match: Match, template: Template, rec: Record) -> None:
        """Under the match lock, once every live seat has answered: open the call, or reveal
        the round and deal the next card."""
        if match.phase == "guess":
            await self._set_clock(match, rec, template)
            self.bus.emit(
                match.id,
                "guess_opened",
                GuessOpened(round_n=match.round_n, state_version=match.state_version),
            )
            return
        await self._settle_round(match, template)
        if match.status != "active":
            return
        await self._set_clock(match, rec, template)
        self._start_model_answers(match, rec)

    async def _settle_round(self, match: Match, template: Template) -> None:
        """Sends out the round's rulings and reveal, then ends the match when it is over."""
        _, rec = await self._load(match.id)
        # Closing a round always deals the next, so the round to reveal is the one before.
        round_n = match.round_n - 1
        card = template.seed_named(match.cards[round_n - 1])
        assert card is not None
        totals = dict(match.points)
        for row in rec.turn_rows:
            if row["round_n"] != round_n or row["scoring"] is None:
                continue
            scoring = ScoringPayload.model_validate(row["scoring"])
            host = HostPayload.model_validate(row["host"])
            earned = self._turn_points(row["scoring"], template, row["outcome"]) or 0
            self.bus.emit(
                match.id,
                "ruling",
                Ruling(
                    seq=row["seq"],
                    round_n=round_n,
                    actor=row["actor"],
                    move_text=row["move_text"],
                    outcome=row["outcome"],
                    scoring=scoring,
                    host=host,
                    points=earned,
                    totals=totals,
                    to_move=match.to_move,
                    round_in_play=match.round_n,
                    state_version=match.state_version,
                ),
            )
        self.bus.emit(
            match.id,
            "round_revealed",
            RoundRevealed(
                round_n=round_n,
                token=card.opening_token,
                emoji=card.opening_emoji,
                detail=card.detail,
                truth=card.hidden,
                guesses=self._guess_views(match, round_n),
                totals=totals,
                state_version=match.state_version,
            ),
        )
        if match.status == "ended":
            await self._emit_match_ended(match, coaching_line=None)

    async def _write_and_judge_model(
        self,
        match: Match,
        template: Template,
        seat: str,
        held: str | None,
        card: Seed,
        lines: list[str],
    ) -> tuple[Judged, str]:
        """A model seat's answer for the round, judged and rulable. Regenerates when the bluff is
        refused or lands on the real meaning, and persists the text so a restart can reuse it."""
        seq = len(match.turns) + 1
        text = held
        if text is None:
            text = await self._stream_opponent_move(
                match, template, seat, card.card_text, card.hidden, silent=True
            )
            await self._hold(match.id, seat, text, match.round_n)
        refusals = 0
        retold = False
        while True:
            reason = layer1(template, text, match)
            if reason is None:
                judged = await self._judge_until_ruled(
                    match, template, seq, text, card.card_text, lines, card.hidden, quiet=True
                )
                hit = judged.response.scoring.truth_proximity == "hit"
                if hit and card.hidden and not retold:
                    # A model seat is meant to bluff: one more try when it wrote the truth.
                    retold = True
                    text = await self._stream_opponent_move(
                        match, template, seat, card.card_text, card.hidden, silent=True
                    )
                    await self._hold(match.id, seat, text, match.round_n)
                    continue
                if judged.outcome != "semantic_reject":
                    return judged, text
            refusals += 1
            if text == template.default_move:
                raise HouseStuck(match.id)
            if refusals < template.strikes_before_consequence:
                text = await self._stream_opponent_move(
                    match, template, seat, card.card_text, card.hidden, silent=True
                )
                await self._hold(match.id, seat, text, match.round_n)
            else:
                text = template.default_move

    async def _hold(self, match_id: str, seat: str, text: str | None, round_n: int) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update seats set held_move = %s, held_round = %s "
                "where match_id = %s and seat = %s",
                (text, round_n, match_id, seat),
            )

    # The clock

    async def expire_clocks(self) -> list[str]:
        """Forfeits every clocked turn past its deadline. Returns the matches it looked at."""
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "select id from matches where status = 'active' and turn_deadline < now()"
                )
            ).fetchall()
        # Each on its own task: a House turn played after one forfeit holds up no other match.
        for row in rows:
            self._spawn(self._expire(row["id"]))
        return [row["id"] for row in rows]

    async def _expire(self, match_id: str) -> None:
        """A judge still working on an answer holds the clock."""
        async with self.locks[match_id]:
            match, rec = await self._load(match_id)
            now = datetime.now(UTC)
            if match.status != "active" or rec.turn_deadline is None or rec.turn_deadline > now:
                return
            await self._expire_turn(match, rec, self.template_of(match))

    async def _expire_turn(self, match: Match, rec: Record, template: Template) -> None:
        if template.mode == "escalation":
            await self._forfeit(match, template, match.to_move)
            if not await self._emit_if_ended(match):
                await self._after_turn(match, template, rec)
            return
        if match.phase == "guess":
            owed = match.owed_guesses()
            for seat in owed:
                skip_guess(match, seat, template)
            async with self.pool.connection() as conn, conn.transaction():
                await self._save(match, conn)
                for guess in match.guesses[-len(owed) :]:
                    await self._insert_guess(conn, match, guess, None)
            await self._round_closed(match, template, rec)
            return
        round_n = match.round_n
        for row in rec.humans:
            if not row.submitted and self._still_owed(match, row.seat, round_n):
                await self._forfeit(match, template, row.seat)
        if match.round_n == round_n and match.phase == "write" and match.status == "active":
            # Only answers still with the judge or a model seat remain; the round waits.
            await self._set_clock(match, rec, template, running=False)
        await self._after_showcase_change(match, template, rec, round_n)
        self.bus.emit(
            match.id,
            "turn_changed",
            TurnChanged(
                to_move=match.to_move,
                turn_deadline=None,
                round_in_play=match.round_n,
                state_version=match.state_version,
            ),
        )

    # The judge and the House

    async def _judge_until_ruled(
        self,
        match: Match,
        template: Template,
        seq: int,
        move_text: str,
        previous: str,
        transcript: list[str],
        hidden: str = "",
        quiet: bool = False,
    ) -> Judged:
        """Retries the judge call until it rules, pausing the match meanwhile unless quiet.
        Raises MatchClosed if the match closes, and JudgeGaveUp after JUDGE_GIVE_UP_S."""
        paused = False
        attempt = 0
        started = time.monotonic()
        while True:
            call = await self.caller.judge(template, transcript, previous, move_text, hidden)
            verdict_id = await self._insert_verdict(call)
            if call.response is not None:
                self.judge_fault = None
                if paused:
                    await self._set_status(match.id, "awaiting_judgment")
                    self.bus.emit(match.id, "judge_resumed", JudgeResumed(seq=seq))
                return Judged(route_outcome(call.response.scoring), call.response, verdict_id)
            if not paused and not quiet:
                paused = True
                await self._set_status(match.id, "paused")
                host_text = template.judge_out_text.strip().format(standing_form=previous)
                self.bus.emit(
                    match.id,
                    "judge_paused",
                    JudgePaused(seq=seq, host_text=host_text, move_text=move_text),
                )
            if call.error_status in BILLING_STATUSES:
                fault = f"judge provider answered HTTP {call.error_status}"
                if self.judge_fault != fault:
                    log.error("%s; retrying every %s s", fault, BILLING_RETRY_S)
                self.judge_fault = fault
                await asyncio.sleep(BILLING_RETRY_S)
            else:
                await asyncio.sleep(PAUSE_BACKOFF_S[min(attempt, len(PAUSE_BACKOFF_S) - 1)])
            attempt += 1
            if await self._status_of(match.id) in ("abandoned", "ended"):
                raise MatchClosed(match.id)
            if time.monotonic() - started > JUDGE_GIVE_UP_S:
                raise JudgeGaveUp(match.id)

    @staticmethod
    def _rejection(
        match: Match,
        template: Template,
        actor: Actor,
        outcome: Literal["deterministic_invalid", "semantic_reject"],
        reason_text: str,
        nudge: str | None = None,
    ) -> TurnRejected:
        strikes = match.strikes[actor]
        if strikes < 2:
            nudge = None
        elif nudge is None:
            target = match.card if template.mode == "showcase" else match.standing_form
            nudge = template.validation_messages.nudge.format(standing_form=target)
        return TurnRejected(
            seat=actor, outcome=outcome, reason_text=reason_text, strikes=strikes, nudge_text=nudge
        )

    def _emit_rejection(self, match: Match, template: Template, rejected: TurnRejected) -> None:
        """A showcase refusal is about a hidden answer, so the stream says only whose it was;
        that seat reads the reason from its own snapshot."""
        if template.mode == "showcase":
            rejected = rejected.model_copy(update={"reason_text": "", "nudge_text": None})
        self.bus.emit(match.id, "turn_rejected", rejected)

    def _slot(self, match_id: str, seat: str) -> int | None:
        """The seat's slot, taking the lowest free one on its first move; None when the House
        has no slots or every slot is taken."""
        key = (match_id, seat)
        if key in self.slots:
            return self.slots[key]
        free = sorted(set(range(self.house_slots)) - set(self.slots.values()))
        if not free:
            return None
        self.slots[key] = free[0]
        return free[0]

    async def _stream_opponent_move(
        self,
        match: Match,
        template: Template,
        seat: str,
        prompt: str,
        hidden: str = "",
        silent: bool = False,
    ) -> str:
        lines = transcript(match, template, finished_only=True)
        if not await self._stood_in(match.id, seat):
            for attempt in range(HOUSE_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(HOUSE_RETRY_S)
                stream = self.caller.opponent_stream(
                    template, seat, prompt, lines, hidden, self._slot(match.id, seat)
                )
                try:
                    return await self._collect_move(match, stream, silent)
                except CallError as e:
                    log.warning("opponent call failed for %s: %s", match.id, e)
            if self.fallback is None:
                return ""
            await self._stand_in_for(match.id, seat)
        assert self.fallback is not None
        stream = self.caller.opponent_stream(
            template, seat, prompt, lines, hidden, spec=self.fallback[1]
        )
        try:
            return await self._collect_move(match, stream, silent)
        except CallError as e:
            log.warning("stand-in call failed for %s: %s", match.id, e)
            return ""

    async def _collect_move(self, match: Match, stream: AsyncIterator[str], silent: bool) -> str:
        seq = len(match.turns) + 1
        parts: list[str] = []
        async for chunk in stream:
            parts.append(chunk)
            if not silent:
                self.bus.emit(match.id, "move_token", MoveToken(seq=seq, text=chunk))
        return clean_move("".join(parts))

    async def _stood_in(self, match_id: str, seat: str) -> bool:
        """Whether the fallback model already holds this House seat."""
        if self.fallback is None:
            return False
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "select model_ref from seats where match_id = %s and seat = %s",
                    (match_id, seat),
                )
            ).fetchone()
        return row is not None and row["model_ref"] == self.fallback[0]

    async def _stand_in_for(self, match_id: str, seat: str) -> None:
        assert self.fallback is not None
        async with self.pool.connection() as conn:
            await conn.execute(
                "update seats set model_ref = %s where match_id = %s and seat = %s",
                (self.fallback[0], match_id, seat),
            )

    def _stand_in(self, model_ref: str | None) -> str | None:
        """The fallback model's name for a move it played in the House's place."""
        if self.fallback is None or model_ref != self.fallback[0]:
            return None
        return self.fallback[1].display_name

    async def _emit_match_ended(self, match: Match, coaching_line: str | None) -> None:
        snap = await self.snapshot(match.id)
        assert match.end_reason is not None
        self._forget(match.id)
        self.bus.emit(
            match.id,
            "match_ended",
            MatchEnded(
                end_reason=match.end_reason,
                winner=match.winner,
                totals={s.seat: s.points for s in snap.seats},
                highlight_seq=self._highlight_seq(snap),
                coaching_line=coaching_line,
                share_text=self._share_text(snap),
                replay_id=match.id,
                state_version=match.state_version,
            ),
        )


def entry_case(text: str) -> str:
    """Dictionary casing for an entry on the table: lowercase start, no closing full stop."""
    text = text.strip().rstrip(".").strip()
    if len(text) > 1 and text[1].islower():
        text = text[0].lower() + text[1:]
    return text
