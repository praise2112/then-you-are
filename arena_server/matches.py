"""Match service: the commands, human and model turns in both modes, the turn clock, restart
recovery and the abandon sweep."""

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from arena_core.state import (
    Actor,
    Change,
    IllegalAction,
    Match,
    apply_guess,
    apply_ruling,
    check_guess,
    check_move,
    check_resign,
    forfeit_turn,
    layer1,
    model_next,
    normalize,
    previous_of,
    repeats_the_round,
    resign,
    skip_guess,
    transcript,
    weighted_total,
)
from arena_core.template import Seed, Template
from arena_judge.caller import ModelCaller, ModelSpec
from arena_judge.schema import HostPayload, ScoringPayload
from arena_server.db import Pool
from arena_server.events import (
    EventBus,
    GuessOpened,
    JudgeStarted,
    MatchEnded,
    RoundRevealed,
    Ruling,
    SeatSubmitted,
    TurnChanged,
    TurnRejected,
)
from arena_server.house import House
from arena_server.judging import Judge, Judged, JudgeGaveUp
from arena_server.presence import Presence, TurnNudge
from arena_server.sessions import seat_of
from arena_server.snapshots import (
    REPEAT_TEXT,
    build_snapshot,
    clock_length,
    guess_views,
    hides_round,
    highlight_seq,
    rejection,
    resolve_pick,
    returned_answer,
    share_text,
    shows_live,
    visible_to,
)
from arena_server.store import (
    MatchClosed,
    MatchError,
    Record,
    TurnRow,
    action_seen,
    add_disagreement,
    close_idle,
    close_unfilled,
    ended_count,
    hold_move,
    live_public_ids,
    load_match,
    overdue_ids,
    replay_ids,
    reset_for_restart,
    save_match,
    set_deadline,
    set_public,
    set_status,
    set_submitted,
    stamp_badges,
    store_guesses,
    store_turn,
)
from arena_server.tables import broadcast_lobby
from arena_server.views import MatchSnapshot, Replay, StageView

STREAM_LINGER_S = 300
GRACE = timedelta(seconds=30)
MODEL_REWRITES = 2


log = logging.getLogger(__name__)


class HouseStuck(Exception):
    """The House could not produce a legal answer, even its default move."""


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
        self.public_base_url = public_base_url
        self.presence = presence
        self.house = House(pool, bus, caller, house_slots, fallback)
        self.judge = Judge(pool, bus, caller, judge_model)
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.tasks: set[asyncio.Task] = set()
        # Showcase model seats writing for a round: (match, seat, round).
        self.answering: set[tuple[str, str, int]] = set()
        # Each match plays the template it was created with, parsed once from its row.
        self.match_templates: dict[str, Template] = {}

    def _forget(self, match_id: str) -> None:
        """Drops the per-match memory once a match is over; the event stream lingers so a
        client can still read the ending."""
        self.locks.pop(match_id, None)
        self.match_templates.pop(match_id, None)
        self.house.release(match_id)
        asyncio.get_running_loop().call_later(STREAM_LINGER_S, self.bus.forget, match_id)

    async def recover(self) -> None:
        """After a restart: a move waiting on the judge is lost, so the player resubmits; model
        seats finish any turn they owed, and a clocked seat gets a fresh deadline."""
        for match_id in await reset_for_restart(self.pool):
            self.spawn(self._resume(match_id))

    async def _resume(self, match_id: str) -> None:
        async with self.locks[match_id]:
            match, rec = await self.load(match_id)
            if match.status != "active":
                return
            template = rec.template
            if template.mode == "escalation":
                await self.after_turn(match, template, rec)
                return
            # Model seats write again when a player next opens or answers this match.
            await self.set_clock(match, rec, template)

    # Loading and saving

    async def load(self, match_id: str) -> tuple[Match, Record]:
        return await load_match(self.pool, match_id, self.templates, self.match_templates)

    async def set_clock(
        self, match: Match, rec: Record, template: Template, running: bool = True
    ) -> str | None:
        """Starts the clock for the turn or phase in play when two or more humans share the
        table; clears it otherwise, or when not running."""
        clocked = running and match.clocked
        deadline = datetime.now(UTC) + clock_length(match, template) if clocked else None
        if deadline is None and rec.turn_deadline is None:
            return None
        await set_deadline(self.pool, match.id, deadline)
        return deadline.isoformat() if deadline else None

    async def close_abandoned(self) -> list[str]:
        """Closes every match idle past the abandon window and every table still unfilled
        past its window. Returns their ids."""
        idle, stale = await close_idle(self.pool)
        unfilled = []
        for match_id in stale:
            # Under the match lock, so a join already under way either lands first or finds
            # the table closed.
            async with self.locks[match_id]:
                if not await close_unfilled(self.pool, match_id):
                    continue
                match, _ = await self.load(match_id)
                await self._emit_match_ended(match, coaching_line=None)
                unfilled.append(match_id)
        for match_id in idle:
            self._forget(match_id)
        if unfilled:
            await broadcast_lobby(self.presence, self.pool, self.templates)
        return [*idle, *unfilled]

    # Snapshots

    async def snapshot(self, match_id: str, session_key: str | None = None) -> MatchSnapshot:
        match, rec = await self.load(match_id)
        return await self._snapshot(match, rec, session_key)

    async def _snapshot(self, match: Match, rec: Record, session_key: str | None) -> MatchSnapshot:
        template = rec.template
        viewer = await seat_of(self.pool, rec, session_key)
        if viewer and template.mode == "showcase" and match.status == "active":
            self.start_model_answers(match, rec)
        hide = hides_round(match, template)
        returned = (
            await returned_answer(self.pool, match, template, rec, viewer)
            if hide and viewer
            else None
        )
        return build_snapshot(
            match, template, rec, viewer, returned, self.opponent_name, self.house.stand_in
        )

    async def replay(self, match_id: str, session_key: str | None = None) -> Replay:
        match, rec = await self.load(match_id)
        snap = await self._snapshot(match, rec, session_key)
        if snap.status not in ("ended", "abandoned"):
            raise MatchError(404, "match still running")
        return Replay(
            **snap.model_dump(),
            share_text=share_text(snap, self.public_base_url),
            highlight_seq=highlight_seq(snap),
            is_curated=rec.is_curated,
        )

    async def stage(self) -> StageView:
        return StageView(
            live=[await self.snapshot(match_id) for match_id in await live_public_ids(self.pool)],
            duels_played=await ended_count(self.pool),
        )

    async def replays(self, sort: Literal["curated", "newest", "longest"]) -> list[Replay]:
        return [await self.replay(match_id) for match_id in await replay_ids(self.pool, sort)]

    # Commands

    async def set_visibility(self, match_id: str, session_key: str, public: bool) -> None:
        _, rec = await self.load(match_id)
        await self._check_owner(rec, session_key)
        await set_public(self.pool, match_id, public)

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
            if await action_seen(self.pool, match_id, action_id):
                return
            match, rec = await self.load(match_id)
            seat = await self._check_command(
                match, rec, session_key, "move", expected_version, round_n
            )
            template = rec.template
            if template.mode == "showcase":
                self.start_model_answers(match, rec)
                await set_submitted(self.pool, match_id, seat, True)
                self.bus.emit(
                    match_id,
                    "seat_submitted",
                    SeatSubmitted(seat=seat, state_version=match.state_version),
                )
                self.spawn(self._answer_as_human(match_id, seat, action_id, move_text))
                return
            await set_status(self.pool, match_id, "awaiting_judgment")
        self.spawn(self._run_human_move(match_id, seat, action_id, move_text))

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
            if await action_seen(self.pool, match_id, action_id):
                return
            match, rec = await self.load(match_id)
            seat = await self._check_command(
                match, rec, session_key, "guess", expected_version, round_n
            )
            template = rec.template
            card = template.seed_named(match.card)
            assert card is not None
            pick = resolve_pick(match, card, seat, key)
            try:
                apply_guess(match, seat, pick, template)
            except IllegalAction as e:
                raise MatchError(409, str(e)) from e
            await store_guesses(self.pool, match, match.guesses[-1:], action_id)
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
            match, rec = await self.load(match_id)
            if await action_seen(self.pool, match_id, action_id):
                return
            seat = await self._check_command(match, rec, session_key, "resign", expected_version)
            template = rec.template
            change = resign(match, seat, template)
            await store_turn(
                self.pool,
                match,
                TurnRow(
                    seat, "", "deterministic_invalid", layer1_result="resign", action_id=action_id
                ),
            )
            if await self._emit_if_ended(match):
                return
            if template.mode == "showcase":
                if change.call_opened or change.round_closed:
                    await self._round_closed(match, template, rec)
                return
            await self.after_turn(match, template, rec)

    async def disagree(self, match_id: str, seq: int, session_key: str | None) -> None:
        """One vote per seated session and move, on a move the table can already see."""
        match, rec = await self.load(match_id)
        template = rec.template
        if session_key is None or await seat_of(self.pool, rec, session_key) is None:
            raise MatchError(403, "only a seat at this table can disagree")
        turn = next((t for t in match.turns if t.seq == seq), None)
        if turn is None or not visible_to(match, template, None, turn.round_n, turn.actor):
            raise MatchError(404, "no such turn")
        previous = previous_of(match, template, turn)
        await add_disagreement(
            self.pool,
            match_id,
            seq,
            session_key,
            match.template_id,
            normalize(previous),
            normalize(turn.move_text),
        )

    async def _check_owner(self, rec: Record, session_key: str) -> None:
        if (await seat_of(self.pool, rec, session_key)) != rec.owner.seat:
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
        seat = await seat_of(self.pool, rec, session_key)
        if seat is None:
            raise MatchError(403, "not your match")
        template = rec.template
        try:
            if action == "resign":
                check_resign(match, seat)
            elif action == "guess":
                check_guess(match, seat, template, round_n)
            else:
                check_move(match, seat, template, round_n)
        except IllegalAction as e:
            raise MatchError(409, str(e)) from e
        if action != "move":
            return seat
        if template.mode == "showcase":
            if rec.row(seat).submitted:
                raise MatchError(409, "you already answered this round")
            return seat
        # A move still with the judge holds the turn.
        if match.status != "active":
            raise MatchError(409, "not your move")
        if expected_version != match.state_version:
            raise MatchError(409, f"stale version: match is at {match.state_version}")
        return seat

    def spawn(self, coro: Any) -> asyncio.Task:
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
            match, rec = await self.load(match_id)
            if match.status != "awaiting_judgment" or match.to_move != seat:
                return
            template = rec.template
            try:
                ended = await self._play_move(match, template, seat, move_text, action_id)
                if not ended:
                    await self.after_turn(match, template, rec)
            except MatchClosed:
                log.info("match %s closed while waiting on the judge", match_id)
            except Exception:
                log.exception("move on %s failed; the match goes back to a human seat", match_id)
                await set_status(self.pool, match_id, "active")
                match, rec = await self.load(match_id)
                if match.to_move in rec.models:
                    await self._forfeit(match, template, match.to_move)
                    if not await self._emit_if_ended(match):
                        await self.after_turn(match, template, rec)
                    return
                await self._give_time(match, rec, template)
                match, rec = await self.load(match_id)
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

    async def after_turn(self, match: Match, template: Template, rec: Record) -> None:
        """Escalation, under the match lock: model seats play until a human seat is to move,
        whose clock then starts."""
        if match.status == "active" and match.to_move in rec.models:
            await self._play_opponent(match, template, rec)
        if match.status != "active":
            return
        deadline = await self.set_clock(match, rec, template)
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
        if match.clocked and mover.session_key:
            await self.presence.send(
                mover.session_key, TurnNudge(match_id=match.id, title=template.title)
            )

    async def _refuse_layer1(
        self, match: Match, template: Template, actor: Actor, move_text: str, action_id: str | None
    ) -> Change | None:
        """Applies a Layer 1 refusal when there is one, and returns its Change."""
        reason = layer1(template, move_text, match)
        if reason is None:
            return None
        change = apply_ruling(match, actor, move_text, "deterministic_invalid", template)
        await store_turn(
            self.pool,
            match,
            TurnRow(
                actor,
                move_text,
                "deterministic_invalid",
                layer1_result=reason,
                action_id=action_id,
                round_n=change.round_n,
            ),
        )
        self._emit_rejection(
            match,
            template,
            rejection(
                match,
                template,
                actor,
                change.strikes,
                "deterministic_invalid",
                getattr(template.validation_messages, reason),
            ),
        )
        await self._store_forfeit(match, change)
        return change

    async def _refuse_semantic(
        self,
        match: Match,
        template: Template,
        actor: Actor,
        move_text: str,
        judged: Judged,
        action_id: str | None,
    ) -> Change:
        change = apply_ruling(match, actor, move_text, "semantic_reject", template)
        await store_turn(
            self.pool,
            match,
            TurnRow(
                actor,
                move_text,
                "semantic_reject",
                verdict_id=judged.verdict_id,
                action_id=action_id,
                round_n=change.round_n,
            ),
        )
        host = judged.response.host
        self._emit_rejection(
            match,
            template,
            rejection(
                match,
                template,
                actor,
                change.strikes,
                "semantic_reject",
                host.headline,
                host.quotable_line,
            ),
        )
        await self._store_forfeit(match, change)
        return change

    async def _forfeit(self, match: Match, template: Template, seat: str) -> None:
        await self._store_forfeit(match, forfeit_turn(match, seat, template))

    async def _store_forfeit(self, match: Match, change: Change) -> None:
        if change.forfeit is None:
            return
        turn = change.forfeit
        await store_turn(
            self.pool,
            match,
            TurnRow(turn.actor, "", "forfeit", seq=turn.seq, layer1_result="forfeit"),
        )

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
            template,
            earned,
            truth_hit=proximity == "hit",
        )
        await store_turn(
            self.pool,
            match,
            TurnRow(
                actor,
                move_text,
                judged.outcome,
                seq=seq,
                verdict_id=judged.verdict_id,
                action_id=action_id,
            ),
        )
        badges = ["close_call"] if judged.outcome == "semantic_uncertain" else []
        if judged.outcome != "fail" and proximity == "hit":
            badges.append("accidental_truth")
        elif judged.outcome != "fail" and proximity == "near":
            badges.append("near_miss")
        if badges:
            await stamp_badges(self.pool, judged.verdict_id, badges)
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
    ) -> bool:
        """Escalation: one move through Layer 1 and the judge. Returns True when the match ended."""
        if await self._refuse_layer1(match, template, actor, move_text, action_id):
            return await self._emit_if_ended(match)
        seq = len(match.turns) + 1
        self.bus.emit(match.id, "judge_started", JudgeStarted(seq=seq))
        judged = await self.judge.rule(
            match, template, seq, move_text, match.standing_form, transcript(match, template)
        )
        if judged.outcome == "semantic_reject":
            await self._refuse_semantic(match, template, actor, move_text, judged, action_id)
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
                step = model_next(match.strikes[seat] - strikes_before, template)
                if step == "write":
                    move_text = await self.house.write(match, template, seat, match.seed)
                elif step == "default_move":
                    move_text = template.default_move
                else:
                    resign(match, seat, template)
                    await save_match(self.pool, match)
                    if match.status == "ended":
                        await self._emit_match_ended(match, coaching_line=None)
                    break
                if await self._play_move(match, template, seat, move_text, None):
                    return

    # Showcase turns

    def start_model_answers(self, match: Match, rec: Record) -> None:
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
            self.spawn(self._answer_as_model(match.id, seat, match.round_n))

    async def _answer_as_model(
        self, match_id: str, seat: str, round_n: int, tries: int = 0
    ) -> None:
        rewrite = False
        try:
            match, rec = await self.load(match_id)
            if not self._still_owed(match, seat, round_n):
                return
            template = rec.template
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
                    match, rec = await self.load(match_id)
                    if match.status != "active" or match.round_n != round_n:
                        return
                    resign(match, seat, template)
                    await save_match(self.pool, match)
                    await self._after_showcase_change(match, template, rec, round_n)
                return
            async with self.locks[match_id]:
                match, rec = await self.load(match_id)
                if not self._still_owed(match, seat, round_n):
                    return
                await hold_move(self.pool, match_id, seat, None, round_n)
                # Two identical entries could not be told apart on the call.
                if repeats_the_round(match, template, text):
                    if tries < MODEL_REWRITES:
                        rewrite = True
                        return
                    resign(match, seat, template)
                    await save_match(self.pool, match)
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
                self.spawn(self._answer_as_model(match_id, seat, round_n, tries + 1))

    async def _answer_as_human(
        self, match_id: str, seat: str, action_id: str, move_text: str
    ) -> None:
        """Showcase: judges one seat's answer without holding up the other seats. Nothing about
        it leaves the server until the round is revealed."""
        try:
            match, rec = await self.load(match_id)
            template = rec.template
            round_n = match.round_n
            card = template.seed_named(match.card)
            assert card is not None
            reason = layer1(template, move_text, match)
            judged = None
            if reason is None:
                judged = await self.judge.rule(
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
                await set_submitted(self.pool, match_id, seat, False)
                match, rec = await self.load(match_id)
                if not self._still_owed(match, seat, round_n):
                    return
                if judged is None:
                    change = await self._refuse_layer1(match, template, seat, move_text, action_id)
                elif judged.outcome == "semantic_reject":
                    change = await self._refuse_semantic(
                        match, template, seat, move_text, judged, action_id
                    )
                elif repeats_the_round(match, template, move_text):
                    # No strike: the seat could not have known.
                    await store_turn(
                        self.pool,
                        match,
                        TurnRow(
                            seat,
                            move_text,
                            "deterministic_invalid",
                            layer1_result="repeat",
                            action_id=action_id,
                        ),
                    )
                    self._emit_rejection(
                        match,
                        template,
                        rejection(
                            match,
                            template,
                            seat,
                            match.strikes[seat],
                            "deterministic_invalid",
                            REPEAT_TEXT,
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
                if change is not None and change.forfeit is not None:
                    await self._after_showcase_change(match, template, rec, round_n)
                else:
                    await self._give_time(match, rec, template)
        except MatchClosed:
            log.info("match %s closed while waiting on the judge", match_id)
        except Exception:
            log.exception("answer on %s failed; the seat may answer again", match_id)
            async with self.locks[match_id]:
                await set_submitted(self.pool, match_id, seat, False)
                match, rec = await self.load(match_id)
                await self._give_time(match, rec, rec.template)

    async def _give_time(self, match: Match, rec: Record, template: Template) -> None:
        """A seat handed back its move after the judge ran long gets a whole clock again."""
        if not match.clocked or match.status not in ("active", "awaiting_judgment"):
            return
        left = rec.turn_deadline - datetime.now(UTC) if rec.turn_deadline else None
        if left is None or left < GRACE:
            await self.set_clock(match, rec, template)

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
            await self.set_clock(match, rec, template)
            self.bus.emit(
                match.id,
                "guess_opened",
                GuessOpened(round_n=match.round_n, state_version=match.state_version),
            )
            return
        await self._settle_round(match, template)
        if match.status != "active":
            return
        await self.set_clock(match, rec, template)
        self.start_model_answers(match, rec)

    async def _settle_round(self, match: Match, template: Template) -> None:
        """Sends out the round's rulings and reveal, then ends the match when it is over."""
        _, rec = await self.load(match.id)
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
                    points=row["points"] or 0,
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
                guesses=guess_views(match, round_n),
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
            text = await self.house.write(
                match, template, seat, card.card_text, card.hidden, silent=True
            )
            await hold_move(self.pool, match.id, seat, text, match.round_n)
        refusals = 0
        retold = False
        while True:
            reason = layer1(template, text, match)
            if reason is None:
                judged = await self.judge.rule(
                    match, template, seq, text, card.card_text, lines, card.hidden, quiet=True
                )
                hit = judged.response.scoring.truth_proximity == "hit"
                if hit and card.hidden and not retold:
                    # A model seat is meant to bluff: one more try when it wrote the truth.
                    retold = True
                    text = await self.house.write(
                        match, template, seat, card.card_text, card.hidden, silent=True
                    )
                    await hold_move(self.pool, match.id, seat, text, match.round_n)
                    continue
                if judged.outcome != "semantic_reject":
                    return judged, text
            refusals += 1
            if text == template.default_move:
                raise HouseStuck(match.id)
            if model_next(refusals, template) == "write":
                text = await self.house.write(
                    match, template, seat, card.card_text, card.hidden, silent=True
                )
                await hold_move(self.pool, match.id, seat, text, match.round_n)
            else:
                text = template.default_move

    # The clock

    async def expire_clocks(self) -> list[str]:
        """Forfeits every clocked turn past its deadline. Returns the matches it looked at."""
        overdue = await overdue_ids(self.pool)
        # Each on its own task: a House turn played after one forfeit holds up no other match.
        for match_id in overdue:
            self.spawn(self._expire(match_id))
        return overdue

    async def _expire(self, match_id: str) -> None:
        """A judge still working on an answer holds the clock."""
        async with self.locks[match_id]:
            match, rec = await self.load(match_id)
            now = datetime.now(UTC)
            if match.status != "active" or rec.turn_deadline is None or rec.turn_deadline > now:
                return
            await self._expire_turn(match, rec, rec.template)

    async def _expire_turn(self, match: Match, rec: Record, template: Template) -> None:
        if template.mode == "escalation":
            await self._forfeit(match, template, match.to_move)
            if not await self._emit_if_ended(match):
                await self.after_turn(match, template, rec)
            return
        if match.phase == "guess":
            owed = match.owed_guesses()
            for seat in owed:
                skip_guess(match, seat, template)
            await store_guesses(self.pool, match, match.guesses[-len(owed) :], None)
            await self._round_closed(match, template, rec)
            return
        round_n = match.round_n
        for row in rec.humans:
            if not row.submitted and self._still_owed(match, row.seat, round_n):
                await self._forfeit(match, template, row.seat)
        if match.round_n == round_n and match.phase == "write" and match.status == "active":
            # Only answers still with the judge or a model seat remain; the round waits.
            await self.set_clock(match, rec, template, running=False)
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

    # Publishing

    def _emit_rejection(self, match: Match, template: Template, rejected: TurnRejected) -> None:
        """A showcase refusal is about a hidden answer, so the stream says only whose it was;
        that seat reads the reason from its own snapshot."""
        if not shows_live(template):
            rejected = rejected.model_copy(update={"reason_text": "", "nudge_text": None})
        self.bus.emit(match.id, "turn_rejected", rejected)

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
                highlight_seq=highlight_seq(snap),
                coaching_line=coaching_line,
                share_text=share_text(snap, self.public_base_url),
                replay_id=match.id,
                state_version=match.state_version,
            ),
        )
