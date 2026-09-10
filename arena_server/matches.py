"""Match service: creates matches, runs the human and opponent turns, persists everything."""

import asyncio
import hashlib
import json
import logging
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from arena_core.state import (
    PLAYERS,
    REFUSED,
    Actor,
    Guess,
    Match,
    Phase,
    Pick,
    StaleVersionError,
    Turn,
    apply_guess,
    apply_ruling,
    layer1,
    normalize,
    resign,
    weighted_total,
)
from arena_core.template import Seed, Template
from arena_judge.caller import JudgeCall, ModelCaller
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
    MoveToken,
    Outcome,
    RoundRevealed,
    Ruling,
    ScoringPayload,
    TurnRejected,
    route_outcome,
)
from arena_server.db import Pool
from arena_server.events import EventBus
from arena_server.leaderboard import account_streaks
from arena_server.views import (
    AccountView,
    MatchSnapshot,
    OpenDuel,
    Replay,
    RoundView,
    SessionView,
    StageView,
    TurnView,
)

PAUSE_BACKOFF_S = (5, 10, 20, 30)
# A judge call refused for credentials or credit will not heal on its own; retry slowly.
BILLING_STATUSES = (401, 402, 403)
BILLING_RETRY_S = 60
ABANDON_WINDOW = timedelta(hours=24)

log = logging.getLogger(__name__)


class MatchError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class MatchClosed(Exception):
    """The match was abandoned while a turn was still waiting on the judge."""


@dataclass
class Judged:
    outcome: Outcome
    response: JudgeResponse
    verdict_id: int


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
    ):
        self.pool = pool
        self.bus = bus
        self.caller = caller
        self.templates = templates
        self.opponent_ref = opponent_ref
        self.opponent_name = opponent_name
        self.judge_model = judge_model
        self.public_base_url = public_base_url
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.tasks: set[asyncio.Task] = set()
        # The House's bluff for the round in play, written and judged while the player types.
        self.held: dict[str, asyncio.Future[tuple[Judged, str]]] = {}
        self.judge_fault: str | None = None

    def template_of(self, match: Match) -> Template:
        return self.templates[match.template_id]

    # Sessions and creation

    async def ensure_session(
        self, session_key: str | None, stage_name: str | None, list_duels: bool | None = None
    ) -> str:
        key = session_key or secrets.token_urlsafe(24)
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into sessions (session_key, stage_name, list_duels) "
                "values (%s, %s, coalesce(%s, false)) on conflict (session_key) do update "
                "set stage_name = coalesce(%s, sessions.stage_name), "
                "list_duels = coalesce(%s, sessions.list_duels)",
                (
                    key,
                    (stage_name or "Challenger")[:40],
                    list_duels,
                    stage_name and stage_name[:40],
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
                        await self._open_duel(m) for m in await self._open_ids(session_key)
                    ],
                )
        return SessionView(stage_name="Challenger", list_duels=False)

    async def _open_ids(self, session_key: str, template_id: str | None = None) -> list[str]:
        """Open matches of the session and, when signed in, of every session on its account."""
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "select m.id from matches m "
                    "join sessions s on s.session_key = m.p1_session_key "
                    "where m.status in ('active', 'awaiting_judgment', 'paused') "
                    "and (s.session_key = %s or s.account_id = "
                    "(select account_id from sessions where session_key = %s)) "
                    "and (%s::text is null or m.template_id = %s) order by m.created_at",
                    (session_key, session_key, template_id, template_id),
                )
            ).fetchall()
        return [row["id"] for row in rows]

    async def _open_duel(self, match_id: str) -> OpenDuel:
        match, _ = await self._load(match_id)
        template = self.template_of(match)
        if template.mode == "showcase":
            line = f"round {match.round_n} of {template.rounds}, {match.card}"
            if match.phase == "guess":
                line += ", your call"
        else:
            form = match.standing_form
            if form.lower().startswith(template.move_constraints.prefix.lower()):
                form = form[len(template.move_constraints.prefix) :]
            line = f"round {len(match.turns) + 1}, {form.rstrip('.')} stands"
        return OpenDuel(id=match.id, title=template.title, line=line)

    async def create(
        self, session_key: str, template_id: str, seed_token: str | None = None
    ) -> MatchSnapshot:
        template = self.templates.get(template_id)
        if template is None:
            raise MatchError(404, "no such template")
        first = template.seed_named(seed_token) if seed_token else None
        if seed_token and first is None:
            raise MatchError(422, "that opening is not in this game")
        open_ids = await self._open_ids(session_key, template.slug)
        if open_ids:
            return await self.snapshot(open_ids[0], session_key)
        cards = self._deal(template, first)
        match_id = secrets.token_urlsafe(8)
        listed = (await self.session_view(session_key)).list_duels
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into matches (id, template_id, template_version, config, seed_token, "
                "seed_emoji, cards, p1_session_key, p2_model_ref, status, is_public) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)",
                (
                    match_id,
                    template.slug,
                    template.schema_version,
                    template.model_dump_json(),
                    cards[0].opening_token,
                    cards[0].opening_emoji,
                    [c.opening_token for c in cards],
                    session_key,
                    self.opponent_ref,
                    listed,
                ),
            )
        if template.mode == "showcase":
            self._prepare_house(match_id)
        return await self.snapshot(match_id, session_key)

    @staticmethod
    def _deal(template: Template, first: Seed | None) -> list[Seed]:
        """One card for an escalation duel, one per round for a showcase."""
        if template.mode == "escalation":
            return [first or secrets.choice(template.seed_pool)]
        rest = [c for c in template.seed_pool if c is not first]
        cards = secrets.SystemRandom().sample(rest, template.move_budget // 2 - bool(first))
        return [first, *cards] if first else cards

    async def set_visibility(self, match_id: str, session_key: str, public: bool) -> None:
        _, extra = await self._load(match_id)
        if extra["p1_session_key"] != session_key:
            raise MatchError(403, "not your match")
        async with self.pool.connection() as conn:
            await conn.execute(
                "update matches set is_public = %s, is_curated = is_curated and %s where id = %s",
                (public, public, match_id),
            )

    # Loading and saving

    async def _load(self, match_id: str) -> tuple[Match, dict]:
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
            session = await (
                await conn.execute(
                    "select stage_name from sessions where session_key = %s",
                    (row["p1_session_key"],),
                )
            ).fetchone()
        match = Match(
            id=row["id"],
            template_id=row["template_id"],
            template_version=row["template_version"],
            cards=row["cards"],
            seed_emoji=row["seed_emoji"],
            status=row["status"],
            state_version=row["state_version"],
            to_move=row["to_move"],
            phase=row["phase"],
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
            strikes={"p1": row["strikes_p1"], "p2": row["strikes_p2"]},
            points={"p1": row["points_p1"], "p2": row["points_p2"]},
            winner=row["winner"],
            end_reason=row["end_reason"],
        )
        extra = {
            "turn_rows": turns,
            "stage_name": session["stage_name"] if session else "Challenger",
            "p1_session_key": row["p1_session_key"],
            "is_public": row["is_public"],
            "is_curated": row["is_curated"],
            "created_at": row["created_at"],
            "held_move": row["held_move"],
        }
        return match, extra

    async def _save(self, match: Match) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update matches set status = %s, state_version = %s, to_move = %s, phase = %s, "
                "winner = %s, end_reason = %s, points_p1 = %s, points_p2 = %s, "
                "strikes_p1 = %s, strikes_p2 = %s, updated_at = now(), "
                "ended_at = case when %s = 'ended' "
                "and ended_at is null then now() else ended_at end where id = %s",
                (
                    match.status,
                    match.state_version,
                    match.to_move,
                    match.phase,
                    match.winner,
                    match.end_reason,
                    match.points["p1"],
                    match.points["p2"],
                    match.strikes["p1"],
                    match.strikes["p2"],
                    match.status,
                    match.id,
                ),
            )

    async def _set_status(self, match_id: str, status: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update matches set status = %s, updated_at = now() where id = %s",
                (status, match_id),
            )

    async def close_abandoned(self) -> list[str]:
        """Closes every match idle for longer than the abandon window. Returns their ids."""
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "update matches set status = 'abandoned', end_reason = 'abandoned', "
                    "ended_at = now(), updated_at = now() "
                    "where status in ('active', 'awaiting_judgment', 'paused') "
                    "and updated_at < now() - %s returning id",
                    (ABANDON_WINDOW,),
                )
            ).fetchall()
        return [row["id"] for row in rows]

    async def _status_of(self, match_id: str) -> str:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("select status from matches where id = %s", (match_id,))
            ).fetchone()
        return row["status"] if row else "abandoned"

    async def _insert_turn(
        self,
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
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "insert into turns (match_id, seq, actor, move_text, layer1_result, outcome, "
                    "live_verdict_id, action_id, round_n) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
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
                    ),
                )
            ).fetchone()
            assert row is not None
            if verdict_id is not None:
                await conn.execute(
                    "update verdicts set turn_id = %s where id = %s", (row["id"], verdict_id)
                )
        return row["id"]

    async def _insert_guess(self, match: Match, guess: Guess, action_id: str) -> None:
        async with self.pool.connection() as conn:
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

    def _points(self, match: Match) -> tuple[int, int]:
        return match.points["p1"], match.points["p2"]

    def _in_call(self, match: Match, round_n: int) -> bool:
        return match.phase == "guess" and round_n == match.round_n

    def _rounds(self, match: Match, template: Template) -> list[RoundView]:
        if template.mode == "escalation":
            return []
        views = []
        for n, token in enumerate(match.cards[: match.round_n], start=1):
            card = template.seed_named(token)
            assert card is not None
            in_call = self._in_call(match, n)
            revealed = len(match.round_turns(n)) == len(PLAYERS) and not in_call
            views.append(
                RoundView(
                    round_n=n,
                    token=token,
                    emoji=card.opening_emoji if revealed else "",
                    detail=card.detail,
                    truth=card.hidden if revealed else None,
                    options=self._options(match, card, "p1") if in_call else [],
                    guesses=self._guess_views(match, n) if revealed else [],
                )
            )
        return views

    @staticmethod
    def _table(match: Match, card: Seed, guesser: Actor) -> dict[str, tuple[Pick, str]]:
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

    def _resolve_pick(self, match: Match, card: Seed, guesser: Actor, key: str) -> Pick:
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

    async def snapshot(self, match_id: str, session_key: str | None = None) -> MatchSnapshot:
        match, extra = await self._load(match_id)
        template = self.template_of(match)
        p1, p2 = self._points(match)
        return MatchSnapshot(
            id=match.id,
            template_id=match.template_id,
            title=template.title,
            mode=template.mode,
            status=match.status,
            state_version=match.state_version,
            phase=match.phase,
            seed_token=match.seed,
            seed_emoji=match.seed_emoji if template.mode == "escalation" else "",
            rounds=self._rounds(match, template),
            stage_name=extra["stage_name"],
            opponent_name=self.opponent_name,
            to_move=match.to_move,
            winner=match.winner,
            end_reason=match.end_reason,
            points_p1=p1,
            points_p2=p2,
            judged_moves=match.judged_moves,
            move_budget=template.move_budget,
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
                )
                for t in extra["turn_rows"]
                # A round being called on keeps both bluffs off the wire until the reveal.
                if not self._in_call(match, t["round_n"])
            ],
            created_at=extra["created_at"].isoformat(),
            is_public=extra["is_public"],
            is_yours=session_key is not None and extra["p1_session_key"] == session_key,
        )

    async def replay(self, match_id: str, session_key: str | None = None) -> Replay:
        _, extra = await self._load(match_id)
        snap = await self.snapshot(match_id, session_key)
        if snap.status not in ("ended", "abandoned"):
            raise MatchError(404, "match still running")
        return Replay(
            **snap.model_dump(),
            share_text=self._share_text(snap),
            highlight_seq=self._highlight_seq(snap),
            is_curated=extra["is_curated"],
        )

    def _turn_points(
        self, scoring: dict[str, Any] | None, template: Template, outcome: str = ""
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
        link = f"{self.public_base_url}/r/{snap.id}"
        if snap.mode == "showcase":
            result = "won" if snap.winner == "p1" else "lost" if snap.winner else "drew"
            cards = " ".join(r.emoji for r in snap.rounds if r.emoji)
            return (
                f"I {result} a duel of {snap.title}, {snap.points_p1} to {snap.points_p2}. "
                f"{cards} {link}"
            )
        chain = [snap.seed_emoji] + [
            t.host.generated_emoji
            for t in snap.transcript
            if t.host and t.outcome in ("accept", "semantic_uncertain")
        ]
        result = "won" if snap.winner == "p1" else "lost"
        return (
            f"I {result} a duel of {snap.title} in {snap.judged_moves} moves. "
            f"{'→'.join(chain)} {link}"
        )

    # Commands

    async def submit_move(
        self, match_id: str, session_key: str, action_id: str, expected_version: int, move_text: str
    ) -> None:
        if await self._action_seen(match_id, action_id):
            return
        match, extra = await self._load(match_id)
        self._check_command(match, extra, session_key, expected_version, "write")
        await self._set_status(match_id, "awaiting_judgment")
        self._spawn(self._run_human_move(match_id, action_id, move_text))

    async def submit_guess(
        self, match_id: str, session_key: str, action_id: str, expected_version: int, key: str
    ) -> None:
        """The player calls the real entry. Settles the round at once: no judge is involved."""
        if await self._action_seen(match_id, action_id):
            return
        match, extra = await self._load(match_id)
        self._check_command(match, extra, session_key, expected_version, "guess")
        async with self.locks[match_id]:
            match, _ = await self._load(match_id)
            template = self.template_of(match)
            card = template.seed_named(match.card)
            assert card is not None
            pick = self._resolve_pick(match, card, "p1", key)
            try:
                apply_guess(match, "p1", pick, expected_version, template)
            except (StaleVersionError, ValueError) as e:
                raise MatchError(409, str(e)) from e
            await self._save(match)
            await self._insert_guess(match, match.guesses[-1], action_id)
            if match.phase == "write":
                await self._settle_round(match, template)

    async def resign_match(
        self, match_id: str, session_key: str, action_id: str, expected_version: int
    ) -> None:
        match, extra = await self._load(match_id)
        self._check_command(match, extra, session_key, expected_version)
        if await self._action_seen(match_id, action_id):
            return
        async with self.locks[match_id]:
            resign(match, "p1", expected_version)
            await self._save(match)
            await self._insert_turn(
                match, "p1", "", "deterministic_invalid", None, "resign", None, action_id
            )
        await self._emit_match_ended(match, coaching_line=None)

    async def disagree(self, match_id: str, seq: int) -> None:
        match, _ = await self._load(match_id)
        template = self.template_of(match)
        turn = next((t for t in match.turns if t.seq == seq), None)
        if turn is None:
            raise MatchError(404, "no such turn")
        if template.mode == "showcase":
            previous = match.cards[turn.round_n - 1]
        else:
            previous = next(
                (t.move_text for t in reversed(match.turns[: seq - 1]) if t.outcome not in REFUSED),
                match.seed,
            )
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into verdict_pairs (template_id, prev_norm, move_norm, disagree_count) "
                "values (%s, %s, %s, 1) on conflict (template_id, prev_norm, move_norm) "
                "do update set disagree_count = verdict_pairs.disagree_count + 1",
                (match.template_id, normalize(previous), normalize(turn.move_text)),
            )

    def _check_command(
        self,
        match: Match,
        extra: dict,
        session_key: str,
        expected_version: int,
        phase: Phase | None = None,
    ) -> None:
        if extra["p1_session_key"] != session_key:
            raise MatchError(403, "not your match")
        if match.status in ("ended", "abandoned"):
            raise MatchError(409, "match already ended")
        if match.status != "active" or match.to_move != "p1":
            raise MatchError(409, "not your move")
        if phase is not None and match.phase != phase:
            raise MatchError(409, "not your move" if phase == "write" else "no call to make")
        if expected_version != match.state_version:
            raise MatchError(409, f"stale version: match is at {match.state_version}")

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

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # Turns

    def _transcript(
        self, match: Match, template: Template, finished_only: bool = False
    ) -> list[str]:
        if template.mode == "escalation":
            return [f"{_player(t.actor)}: {t.move_text}" for t in match.turns]
        lines = []
        for n, token in enumerate(match.cards, start=1):
            turns = match.round_turns(n)
            if not turns or (finished_only and len(turns) < 2):
                break
            card = template.seed_named(token)
            assert card is not None
            lines.append(f"round {n}, prompt: {card.card_text}")
            lines.extend(f"{_player(t.actor)}: {t.move_text}" for t in turns)
        return lines

    async def _run_human_move(self, match_id: str, action_id: str, move_text: str) -> None:
        async with self.locks[match_id]:
            match, extra = await self._load(match_id)
            template = self.template_of(match)
            try:
                if template.mode == "showcase":
                    await self._play_round(
                        match, template, extra["held_move"], move_text, action_id
                    )
                    return
                ended = await self._play_move(match, template, "p1", move_text, action_id)
                if not ended and match.to_move == "p2":
                    await self._play_opponent(match, template)
            except MatchClosed:
                log.info("match %s abandoned while waiting on the judge", match_id)

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
        await self._save(match)
        await self._insert_turn(
            match, actor, move_text, "deterministic_invalid", None, reason, None, action_id
        )
        self._emit_rejection(
            match,
            template,
            actor,
            "deterministic_invalid",
            getattr(template.validation_messages, reason),
        )
        return True

    async def _refuse_semantic(
        self,
        match: Match,
        template: Template,
        actor: Actor,
        move_text: str,
        judged: "Judged",
        action_id: str | None,
    ) -> None:
        apply_ruling(match, actor, move_text, "semantic_reject", match.state_version, template)
        await self._save(match)
        await self._insert_turn(
            match, actor, move_text, "semantic_reject", None, None, judged.verdict_id, action_id
        )
        host = judged.response.host
        self._emit_rejection(
            match, template, actor, "semantic_reject", host.headline, host.quotable_line
        )

    async def _record_ruling(
        self,
        match: Match,
        template: Template,
        actor: Actor,
        move_text: str,
        judged: "Judged",
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
        await self._save(match)
        await self._insert_turn(
            match, actor, move_text, judged.outcome, seq, None, judged.verdict_id, action_id
        )
        badges = ["close_call"] if judged.outcome == "semantic_uncertain" else []
        if judged.outcome != "fail" and proximity == "hit":
            badges.append("accidental_truth")
        elif judged.outcome != "fail" and proximity == "near":
            badges.append("near_miss")
        if badges:
            await self._stamp_badges(judged.verdict_id, badges)
        p1, p2 = self._points(match)
        return Ruling(
            seq=seq,
            round_n=match.turns[-1].round_n,
            actor=actor,
            move_text=move_text,
            outcome=judged.outcome,
            scoring=response.scoring,
            host=response.host.model_copy(update={"badges": badges}),
            badges=badges,
            points=match.points[actor] - before,
            points_p1=p1,
            points_p2=p2,
            to_move=match.to_move,
            state_version=match.state_version,
        )

    async def _play_move(
        self, match: Match, template: Template, actor: Actor, move_text: str, action_id: str | None
    ) -> bool:
        """Escalation: one move through Layer 1 and the judge. Returns True when the match ended."""
        if await self._refuse_layer1(match, template, actor, move_text, action_id):
            return False
        seq = len(match.turns) + 1
        self.bus.emit(match.id, "judge_started", JudgeStarted(seq=seq))
        judged = await self._judge_until_ruled(
            match, template, seq, move_text, match.standing_form, self._transcript(match, template)
        )
        if judged.outcome == "semantic_reject":
            await self._refuse_semantic(match, template, actor, move_text, judged, action_id)
            return False
        ruling = await self._record_ruling(
            match, template, actor, move_text, judged, seq, action_id
        )
        self.bus.emit(match.id, "ruling", ruling)
        if match.status == "ended":
            coaching = judged.response.host.coaching_line if judged.outcome == "fail" else None
            await self._emit_match_ended(match, coaching)
            return True
        return False

    async def _play_round(
        self, match: Match, template: Template, held: str | None, move_text: str, action_id: str
    ) -> None:
        """Showcase: both answers are judged together. The rulings stay held until the
        round is revealed, which waits on the player's call when the game has one."""
        if await self._refuse_layer1(match, template, "p1", move_text, action_id):
            return
        card = template.seed_named(match.card)
        assert card is not None
        seq = len(match.turns) + 1
        self.bus.emit(match.id, "judge_started", JudgeStarted(seq=seq))
        transcript = self._transcript(match, template)
        house_future = self.held.pop(match.id, None)
        if house_future is None:
            house_future = asyncio.ensure_future(
                self._write_and_judge_house(match, template, held, seq + 1, card, transcript)
            )
        human, house = await asyncio.gather(
            self._judge_until_ruled(
                match, template, seq, move_text, card.card_text, transcript, card.hidden
            ),
            house_future,
        )
        if human.outcome == "semantic_reject":
            await self._refuse_semantic(match, template, "p1", move_text, human, action_id)
            keep: asyncio.Future[tuple[Judged, str]] = asyncio.get_running_loop().create_future()
            keep.set_result(house)
            self.held[match.id] = keep
            return
        await self._record_ruling(
            match, template, "p1", move_text, human, seq, action_id, card.hidden
        )
        await self._record_ruling(
            match, template, "p2", house[1], house[0], seq + 1, None, card.hidden
        )
        await self._hold(match.id, None)
        if match.phase == "guess":
            self.bus.emit(
                match.id,
                "guess_opened",
                GuessOpened(
                    round_n=match.round_n,
                    options=self._options(match, card, "p1"),
                    state_version=match.state_version,
                ),
            )
            return
        await self._settle_round(match, template)

    async def _settle_round(self, match: Match, template: Template) -> None:
        """Sends out the round's rulings and reveal, then ends the match or deals the next card."""
        _, extra = await self._load(match.id)
        round_n = match.turns[-1].round_n
        card = template.seed_named(match.cards[round_n - 1])
        assert card is not None
        p1, p2 = self._points(match)
        for row in extra["turn_rows"]:
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
                    badges=host.badges,
                    points=earned,
                    points_p1=p1,
                    points_p2=p2,
                    to_move=match.to_move,
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
                points_p1=p1,
                points_p2=p2,
                state_version=match.state_version,
            ),
        )
        if match.status == "ended":
            await self._emit_match_ended(match, coaching_line=None)
            return
        self._prepare_house(match.id)

    async def _write_and_judge_house(
        self,
        match: Match,
        template: Template,
        held: str | None,
        seq: int,
        card: Seed,
        transcript: list[str],
    ) -> tuple["Judged", str]:
        """The House's answer for the round, judged and rulable. Regenerates when the bluff is
        refused or lands on the real meaning, and persists the text so a restart can reuse it."""
        text = held
        if text is None:
            text = await self._stream_opponent_move(
                match, template, card.card_text, card.hidden, silent=True
            )
            await self._hold(match.id, text)
        refusals = 0
        retold = False
        while True:
            reason = layer1(template, text, match)
            if reason is None:
                judged = await self._judge_until_ruled(
                    match, template, seq, text, card.card_text, transcript, card.hidden
                )
                hit = judged.response.scoring.truth_proximity == "hit"
                if hit and card.hidden and not retold:
                    # The House is meant to bluff: one more try when it wrote the truth.
                    retold = True
                    text = await self._stream_opponent_move(
                        match, template, card.card_text, card.hidden, silent=True
                    )
                    await self._hold(match.id, text)
                    continue
                if judged.outcome != "semantic_reject":
                    return judged, text
            refusals += 1
            if refusals < template.strikes_before_consequence:
                text = await self._stream_opponent_move(
                    match, template, card.card_text, card.hidden, silent=True
                )
                await self._hold(match.id, text)
            else:
                text = template.default_move

    def _prepare_house(self, match_id: str) -> None:
        """Starts the House writing and being judged for the round in play, so the player's
        submit waits only on their own ruling. Hidden until the reveal."""

        async def prepare() -> tuple[Judged, str]:
            match, _ = await self._load(match_id)
            template = self.template_of(match)
            card = template.seed_named(match.card)
            assert card is not None
            seq = len(match.turns) + 2
            return await self._write_and_judge_house(
                match, template, None, seq, card, self._transcript(match, template)
            )

        task = asyncio.create_task(prepare())
        self.held[match_id] = task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _hold(self, match_id: str, text: str | None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("update matches set held_move = %s where id = %s", (text, match_id))

    async def _judge_until_ruled(
        self,
        match: Match,
        template: Template,
        seq: int,
        move_text: str,
        previous: str,
        transcript: list[str],
        hidden: str = "",
    ) -> Judged:
        """Retries the same judge call while the match sits paused, until a ruling lands.

        Raises MatchClosed once the abandon sweep has closed the match."""
        paused = False
        attempt = 0
        while True:
            call = await self.caller.judge(template, transcript, previous, move_text, hidden)
            verdict_id = await self._insert_verdict(call)
            if call.response is not None:
                self.judge_fault = None
                if paused:
                    await self._set_status(match.id, "awaiting_judgment")
                    self.bus.emit(match.id, "judge_resumed", JudgeResumed(seq=seq))
                return Judged(route_outcome(call.response.scoring), call.response, verdict_id)
            if not paused:
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
            if await self._status_of(match.id) == "abandoned":
                raise MatchClosed(match.id)

    def _emit_rejection(
        self,
        match: Match,
        template: Template,
        actor: Actor,
        outcome: Literal["deterministic_invalid", "semantic_reject"],
        reason_text: str,
        nudge: str | None = None,
    ) -> None:
        strikes = match.strikes[actor]
        if strikes >= 2 and nudge is None:
            target = match.card if template.mode == "showcase" else match.standing_form
            nudge = template.validation_messages.nudge.format(standing_form=target)
        self.bus.emit(
            match.id,
            "turn_rejected",
            TurnRejected(
                outcome=outcome,
                reason_text=reason_text,
                strikes=strikes,
                nudge_text=nudge if strikes >= 2 else None,
            ),
        )

    async def _play_opponent(self, match: Match, template: Template) -> None:
        strikes_before = match.strikes["p2"]
        while match.status == "active" and match.to_move == "p2":
            refusals = match.strikes["p2"] - strikes_before
            if refusals < template.strikes_before_consequence:
                move_text = await self._stream_opponent_move(match, template, match.seed)
            elif refusals == template.strikes_before_consequence:
                move_text = template.default_move
            else:
                resign(match, "p2", match.state_version)
                await self._save(match)
                await self._emit_match_ended(match, coaching_line=None)
                return
            if await self._play_move(match, template, "p2", move_text, None):
                return

    async def _stream_opponent_move(
        self, match: Match, template: Template, prompt: str, hidden: str = "", silent: bool = False
    ) -> str:
        seq = len(match.turns) + 1
        parts: list[str] = []
        transcript = self._transcript(match, template, finished_only=True)
        try:
            async for chunk in self.caller.opponent_stream(template, prompt, transcript, hidden):
                parts.append(chunk)
                if not silent:
                    self.bus.emit(match.id, "move_token", MoveToken(seq=seq, text=chunk))
        except Exception:
            # A dead opponent call is a refusal: the strike path swaps in the default move.
            return ""
        return "".join(parts).strip().strip('"')

    async def _emit_match_ended(self, match: Match, coaching_line: str | None) -> None:
        snap = await self.snapshot(match.id)
        assert match.end_reason is not None
        self.bus.emit(
            match.id,
            "match_ended",
            MatchEnded(
                end_reason=match.end_reason,
                winner=match.winner,
                points_p1=snap.points_p1,
                points_p2=snap.points_p2,
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


def _player(actor: Actor) -> str:
    return "player1" if actor == "p1" else "player2"
