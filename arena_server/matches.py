"""Match service: creates matches, runs the human and opponent turns, persists everything."""

import asyncio
import secrets
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from arena_core.state import (
    REFUSED,
    Actor,
    Match,
    Turn,
    apply_ruling,
    layer1,
    normalize,
    resign,
    weighted_total,
)
from arena_core.template import Template
from arena_judge.caller import JudgeCall, ModelCaller
from arena_judge.schema import (
    JudgePaused,
    JudgeResponse,
    JudgeResumed,
    JudgeStarted,
    MatchEnded,
    MoveToken,
    Outcome,
    Ruling,
    TurnRejected,
    route_outcome,
)
from arena_server.db import Pool
from arena_server.events import EventBus
from arena_server.views import MatchSnapshot, Replay, TurnView

PAUSE_BACKOFF_S = (5, 10, 20, 30)


class MatchError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


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
        template: Template,
        opponent_ref: str,
        opponent_name: str,
        judge_model: str,
        public_base_url: str,
    ):
        self.pool = pool
        self.bus = bus
        self.caller = caller
        self.template = template
        self.opponent_ref = opponent_ref
        self.opponent_name = opponent_name
        self.judge_model = judge_model
        self.public_base_url = public_base_url
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.tasks: set[asyncio.Task] = set()

    # Sessions and creation

    async def ensure_session(self, session_key: str | None, stage_name: str | None) -> str:
        key = session_key or secrets.token_urlsafe(24)
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into sessions (session_key, stage_name) values (%s, %s) "
                "on conflict (session_key) do update "
                "set stage_name = coalesce(%s, sessions.stage_name)",
                (key, (stage_name or "Challenger")[:40], stage_name and stage_name[:40]),
            )
        return key

    async def create(self, session_key: str) -> MatchSnapshot:
        seed = secrets.choice(self.template.seed_pool)
        match_id = secrets.token_urlsafe(8)
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into matches (id, template_id, template_version, config, seed_token, "
                "seed_emoji, p1_session_key, p2_model_ref, status) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, 'active')",
                (
                    match_id,
                    self.template.slug,
                    self.template.schema_version,
                    self.template.model_dump_json(),
                    seed.opening_token,
                    seed.opening_emoji,
                    session_key,
                    self.opponent_ref,
                ),
            )
        return await self.snapshot(match_id)

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
            seed=row["seed_token"],
            seed_emoji=row["seed_emoji"],
            status=row["status"],
            state_version=row["state_version"],
            to_move=row["to_move"],
            turns=[Turn(t["seq"], t["actor"], t["move_text"], t["outcome"]) for t in turns],
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
            "created_at": row["created_at"],
        }
        return match, extra

    async def _save(self, match: Match) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update matches set status = %s, state_version = %s, to_move = %s, "
                "winner = %s, end_reason = %s, points_p1 = %s, points_p2 = %s, "
                "strikes_p1 = %s, strikes_p2 = %s, ended_at = case when %s = 'ended' "
                "and ended_at is null then now() else ended_at end where id = %s",
                (
                    match.status,
                    match.state_version,
                    match.to_move,
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
            await conn.execute("update matches set status = %s where id = %s", (status, match_id))

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
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "insert into turns (match_id, seq, actor, move_text, layer1_result, outcome, "
                    "live_verdict_id, action_id) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
                    (
                        match.id,
                        seq,
                        actor,
                        move_text,
                        layer1_result,
                        outcome,
                        verdict_id,
                        action_id,
                    ),
                )
            ).fetchone()
            assert row is not None
            if verdict_id is not None:
                await conn.execute(
                    "update verdicts set turn_id = %s where id = %s", (row["id"], verdict_id)
                )
        return row["id"]

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
        return round(match.points["p1"]), round(match.points["p2"])

    async def snapshot(self, match_id: str) -> MatchSnapshot:
        match, extra = await self._load(match_id)
        p1, p2 = self._points(match)
        return MatchSnapshot(
            id=match.id,
            template_id=match.template_id,
            status=match.status,
            state_version=match.state_version,
            seed_token=match.seed,
            seed_emoji=match.seed_emoji,
            stage_name=extra["stage_name"],
            opponent_name=self.opponent_name,
            to_move=match.to_move,
            winner=match.winner,
            end_reason=match.end_reason,
            points_p1=p1,
            points_p2=p2,
            judged_moves=match.judged_moves,
            move_budget=self.template.move_budget,
            transcript=[
                TurnView(
                    seq=t["seq"],
                    actor=t["actor"],
                    move_text=t["move_text"],
                    outcome=t["outcome"],
                    scoring=t["scoring"],
                    host=t["host"],
                    points=self._turn_points(t["scoring"]),
                )
                for t in extra["turn_rows"]
            ],
            created_at=extra["created_at"].isoformat(),
        )

    async def replay(self, match_id: str) -> Replay:
        snap = await self.snapshot(match_id)
        if snap.status not in ("ended", "abandoned"):
            raise MatchError(404, "match still running")
        return Replay(
            **snap.model_dump(),
            share_text=self._share_text(snap),
            highlight_seq=self._highlight_seq(snap),
        )

    def _turn_points(self, scoring: dict[str, Any] | None) -> int | None:
        if scoring is None:
            return None
        return round(weighted_total(scoring["scores"], self.template.weights))

    def _highlight_seq(self, snap: MatchSnapshot) -> int | None:
        winner_moves = [
            t
            for t in snap.transcript
            if t.actor == snap.winner and t.scoring and t.outcome != "fail"
        ]
        best = max(
            winner_moves,
            key=lambda t: weighted_total(t.scoring.scores, self.template.weights),  # type: ignore[union-attr]
            default=None,
        )
        return best.seq if best else None

    async def curated(self) -> list[Replay]:
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "select id from matches where is_curated and status = 'ended' "
                    "order by ended_at desc limit 12"
                )
            ).fetchall()
        return [await self.replay(r["id"]) for r in rows]

    def _share_text(self, snap: MatchSnapshot) -> str:
        chain = [snap.seed_emoji] + [
            t.host.generated_emoji
            for t in snap.transcript
            if t.host and t.outcome in ("accept", "semantic_uncertain")
        ]
        result = "won" if snap.winner == "p1" else "lost"
        return (
            f"I {result} a duel of {self.template.title} in {snap.judged_moves} moves. "
            f"{'→'.join(chain)} {self.public_base_url}/r/{snap.id}"
        )

    # Commands

    async def submit_move(
        self, match_id: str, session_key: str, action_id: str, expected_version: int, move_text: str
    ) -> None:
        match, extra = await self._load(match_id)
        self._check_command(match, extra, session_key, expected_version)
        if await self._action_seen(match_id, action_id):
            return
        await self._set_status(match_id, "awaiting_judgment")
        self._spawn(self._run_human_move(match_id, action_id, move_text))

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
        turn = next((t for t in match.turns if t.seq == seq), None)
        if turn is None:
            raise MatchError(404, "no such turn")
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
        self, match: Match, extra: dict, session_key: str, expected_version: int
    ) -> None:
        if extra["p1_session_key"] != session_key:
            raise MatchError(403, "not your match")
        if match.status in ("ended", "abandoned"):
            raise MatchError(409, "match already ended")
        if match.status != "active" or match.to_move != "p1":
            raise MatchError(409, "not your move")
        if expected_version != match.state_version:
            raise MatchError(409, f"stale version: match is at {match.state_version}")

    async def _action_seen(self, match_id: str, action_id: str) -> bool:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "select 1 from turns where match_id = %s and action_id = %s",
                    (match_id, action_id),
                )
            ).fetchone()
        return row is not None

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # Turns

    def _transcript(self, match: Match) -> list[str]:
        return [
            f"{'player1' if t.actor == 'p1' else 'player2'}: {t.move_text}" for t in match.turns
        ]

    async def _run_human_move(self, match_id: str, action_id: str, move_text: str) -> None:
        async with self.locks[match_id]:
            match, _ = await self._load(match_id)
            ended = await self._play_move(match, "p1", move_text, action_id)
            if not ended and match.to_move == "p2":
                await self._play_opponent(match)

    async def _play_move(
        self, match: Match, actor: Actor, move_text: str, action_id: str | None
    ) -> bool:
        """Runs one move through Layer 1 and the judge. Returns True when the match ended."""
        version = match.state_version
        reason = layer1(self.template, move_text, match)
        if reason:
            apply_ruling(
                match, actor, move_text, "deterministic_invalid", version, self.template.move_budget
            )
            await self._save(match)
            await self._insert_turn(
                match, actor, move_text, "deterministic_invalid", None, reason, None, action_id
            )
            self._emit_rejection(
                match,
                actor,
                "deterministic_invalid",
                getattr(self.template.validation_messages, reason),
            )
            return False

        seq = len(match.turns) + 1
        self.bus.emit(match.id, "judge_started", JudgeStarted(seq=seq))
        judged = await self._judge_until_ruled(match, seq, move_text)
        outcome, response = judged.outcome, judged.response

        if outcome == "semantic_reject":
            apply_ruling(match, actor, move_text, outcome, version, self.template.move_budget)
            await self._save(match)
            await self._insert_turn(
                match, actor, move_text, outcome, None, None, judged.verdict_id, action_id
            )
            self._emit_rejection(
                match, actor, outcome, response.host.headline, response.host.quotable_line
            )
            return False

        points = weighted_total(response.scoring.scores, self.template.weights)
        apply_ruling(match, actor, move_text, outcome, version, self.template.move_budget, points)
        await self._save(match)
        await self._insert_turn(
            match, actor, move_text, outcome, seq, None, judged.verdict_id, action_id
        )
        badges = ["close_call"] if outcome == "semantic_uncertain" else []
        p1, p2 = self._points(match)
        self.bus.emit(
            match.id,
            "ruling",
            Ruling(
                seq=seq,
                actor=actor,
                move_text=move_text,
                outcome=outcome,
                scoring=response.scoring,
                host=response.host.model_copy(update={"badges": badges}),
                badges=badges,
                points=round(points),
                points_p1=p1,
                points_p2=p2,
                to_move=match.to_move,
                state_version=match.state_version,
            ),
        )
        if match.status == "ended":
            coaching = response.host.coaching_line if outcome == "fail" else None
            await self._emit_match_ended(match, coaching)
            return True
        return False

    async def _judge_until_ruled(self, match: Match, seq: int, move_text: str) -> Judged:
        """Retries the same judge call while the match sits paused, until a ruling lands."""
        transcript = self._transcript(match)
        previous = match.standing_form
        paused = False
        attempt = 0
        while True:
            call = await self.caller.judge(self.template, transcript, previous, move_text)
            verdict_id = await self._insert_verdict(call)
            if call.response is not None:
                if paused:
                    await self._set_status(match.id, "awaiting_judgment")
                    self.bus.emit(match.id, "judge_resumed", JudgeResumed(seq=seq))
                return Judged(route_outcome(call.response.scoring), call.response, verdict_id)
            if not paused:
                paused = True
                await self._set_status(match.id, "paused")
                host_text = self.template.judge_out_text.strip().format(standing_form=previous)
                self.bus.emit(match.id, "judge_paused", JudgePaused(seq=seq, host_text=host_text))
            await asyncio.sleep(PAUSE_BACKOFF_S[min(attempt, len(PAUSE_BACKOFF_S) - 1)])
            attempt += 1

    def _emit_rejection(
        self,
        match: Match,
        actor: Actor,
        outcome: Literal["deterministic_invalid", "semantic_reject"],
        reason_text: str,
        nudge: str | None = None,
    ) -> None:
        strikes = match.strikes[actor]
        if strikes >= 2 and nudge is None:
            nudge = self.template.validation_messages.nudge.format(
                standing_form=match.standing_form
            )
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

    async def _play_opponent(self, match: Match) -> None:
        strikes_before = match.strikes["p2"]
        while match.status == "active" and match.to_move == "p2":
            refusals = match.strikes["p2"] - strikes_before
            if refusals < self.template.strikes_before_consequence:
                move_text = await self._stream_opponent_move(match)
            elif refusals == self.template.strikes_before_consequence:
                move_text = self.template.default_move
            else:
                resign(match, "p2", match.state_version)
                await self._save(match)
                await self._emit_match_ended(match, coaching_line=None)
                return
            if await self._play_move(match, "p2", move_text, None):
                return

    async def _stream_opponent_move(self, match: Match) -> str:
        seq = len(match.turns) + 1
        parts: list[str] = []
        try:
            async for chunk in self.caller.opponent_stream(
                self.template, match.seed, self._transcript(match)
            ):
                parts.append(chunk)
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
