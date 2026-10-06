"""Match service: the commands, one turn pipeline for human and House seats in both modes, the
turn clock, restart recovery and the abandon sweep."""

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from arena_core.state import (
    FINISHED,
    Change,
    IllegalAction,
    Layer1Reason,
    Match,
    Turn,
    apply_guess,
    apply_ruling,
    card_in_play,
    check_guess,
    check_move,
    check_resign,
    forfeit_turn,
    judged_against,
    layer1,
    model_next,
    normalize,
    owed,
    previous_of,
    refuse_repeat,
    repeats_the_round,
    resign,
    result_kind,
    skip_guess,
    transcript,
    weighted_total,
)
from arena_core.template import Template
from arena_judge.caller import ModelCaller, ModelSpec
from arena_judge.schema import HostPayload, JudgeResponse, ScoringPayload
from arena_server.db import Pool
from arena_server.events import (
    EventBus,
    GuessOpened,
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
    SeatRow,
    TurnRow,
    action_seen,
    add_disagreement,
    close_idle,
    close_unfilled,
    ended_count,
    hold_move,
    live_public_ids,
    load_match,
    load_matches,
    match_is_live,
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
    store_turns,
)
from arena_server.tables import broadcast_lobby
from arena_server.views import MatchSnapshot, Replay, StageView

STREAM_LINGER_S = 300
GRACE = timedelta(seconds=30)
MODEL_REWRITES = 2


log = logging.getLogger(__name__)


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
        stand_in: tuple[str, ModelSpec] | None = None,
    ):
        self.pool = pool
        self.bus = bus
        self.caller = caller
        self.templates = templates
        self.opponent_ref = opponent_ref
        self.opponent_name = opponent_name
        self.public_base_url = public_base_url
        self.presence = presence
        self.house = House(pool, bus, caller, house_slots, stand_in)
        self.judge = Judge(pool, bus, caller, judge_model)
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.tasks: set[asyncio.Task] = set()
        # House seats writing for a hidden round: (match, seat, round).
        self.answering: set[tuple[str, str, int]] = set()
        # Matches with a lapsed clock already queued for the lock.
        self.expiring: set[str] = set()
        # The template each live match plays, parsed once from its row.
        self.match_templates: dict[str, Template] = {}

    def _forget(self, match_id: str) -> None:
        """Drops the per-match memory once a match is over; the event stream lingers so a
        client can still read the ending."""
        self.locks.pop(match_id, None)
        self.match_templates.pop(match_id, None)
        self.house.release(match_id)
        asyncio.get_running_loop().call_later(STREAM_LINGER_S, self.bus.forget, match_id)

    async def lock(self, match_id: str) -> asyncio.Lock:
        """The match's lock. A match that is over or missing gets a lock kept nowhere, since
        nothing can change it."""
        if match_id in self.locks or await match_is_live(self.pool, match_id):
            return self.locks[match_id]
        return asyncio.Lock()

    async def recover(self) -> None:
        """After a restart: a move waiting on the judge is lost, so the player resubmits; every
        active match then picks up the work it owes."""
        for match_id in await reset_for_restart(self.pool):
            self.spawn(self.resume(match_id))

    async def resume(self, match_id: str) -> None:
        """Starts the match's owed work when a House seat owes an answer or a clock runs."""
        async with await self.lock(match_id):
            match, rec = await self.load(match_id)
            house_owes = any(seat in rec.models for seat in owed(match, rec.template))
            if match.status == "active" and (house_owes or match.clocked):
                await self.start(match, rec)

    async def start(self, match: Match, rec: Record) -> None:
        """Under the match lock, when play starts or resumes: a hidden round's clock starts, and
        the match is driven on."""
        if not shows_live(rec.template):
            await self._set_clock(match, rec)
        await self._drive(match, rec)

    # Loading and saving

    async def load(self, match_id: str) -> tuple[Match, Record]:
        return await load_match(self.pool, match_id, self.templates, self.match_templates)

    async def load_many(self, match_ids: list[str]) -> list[tuple[Match, Record]]:
        return await load_matches(self.pool, match_ids, self.templates, self.match_templates)

    async def _set_clock(self, match: Match, rec: Record) -> str | None:
        """Starts the clock for the turn or phase in play when two or more humans share the
        table; clears it otherwise."""
        deadline = datetime.now(UTC) + clock_length(match, rec.template) if match.clocked else None
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
            async with await self.lock(match_id):
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
        # The cursor is read before the load: every event is emitted after its change is stored.
        event_id = self.bus.cursor(match_id)
        match, rec = await self.load(match_id)
        return await self._snapshot(match, rec, session_key, event_id)

    async def _snapshot(
        self, match: Match, rec: Record, session_key: str | None, event_id: str
    ) -> MatchSnapshot:
        viewer = await seat_of(self.pool, rec, session_key)
        hide = hides_round(match, rec.template)
        returned = await returned_answer(self.pool, match, rec, viewer) if hide and viewer else None
        return build_snapshot(
            match,
            rec,
            viewer,
            returned,
            self.opponent_name,
            self.house.stand_in_name,
            event_id,
        )

    async def replay(self, match_id: str, session_key: str | None = None) -> Replay:
        event_id = self.bus.cursor(match_id)
        match, rec = await self.load(match_id)
        return await self._replay(match, rec, session_key, event_id)

    async def _replay(
        self, match: Match, rec: Record, session_key: str | None, event_id: str
    ) -> Replay:
        snap = await self._snapshot(match, rec, session_key, event_id)
        if snap.status not in FINISHED:
            raise MatchError(404, "match still running")
        assert match.end_reason is not None
        return Replay(
            **snap.model_dump(),
            result_kind=result_kind(match.end_reason, match.winner),
            share_text=share_text(snap, self.public_base_url),
            highlight_seq=highlight_seq(snap),
            is_curated=rec.is_curated,
        )

    async def stage(self) -> StageView:
        ids = await live_public_ids(self.pool)
        cursors = {match_id: self.bus.cursor(match_id) for match_id in ids}
        live = [
            await self._snapshot(match, rec, None, cursors[match.id])
            for match, rec in await self.load_many(ids)
        ]
        return StageView(live=live, duels_played=await ended_count(self.pool))

    async def replays(self, sort: Literal["curated", "newest", "longest"]) -> list[Replay]:
        ids = await replay_ids(self.pool, sort)
        cursors = {match_id: self.bus.cursor(match_id) for match_id in ids}
        loaded = await self.load_many(ids)
        return [await self._replay(match, rec, None, cursors[match.id]) for match, rec in loaded]

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
        async with await self.lock(match_id):
            if await action_seen(self.pool, match_id, action_id):
                return
            match, rec = await self.load(match_id)
            seat = await self._check_command(
                match, rec, session_key, "move", expected_version, round_n
            )
            template = rec.template
            # The answer is pending until it lands; in serial play it also holds the turn.
            await set_submitted(self.pool, match_id, seat, True)
            if shows_live(template):
                await set_status(self.pool, match_id, "awaiting_judgment")
            else:
                self._seat_submitted(match, seat)
        self.spawn(self._answer(match_id, template, seat, match.round_n, action_id, move_text))

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
        async with await self.lock(match_id):
            if await action_seen(self.pool, match_id, action_id):
                return
            match, rec = await self.load(match_id)
            seat = await self._check_command(
                match, rec, session_key, "guess", expected_version, round_n
            )
            template = rec.template
            pick = resolve_pick(match, card_in_play(match, template), seat, key)
            try:
                change = apply_guess(match, seat, pick, template)
            except IllegalAction as e:
                raise MatchError(409, str(e)) from e
            await store_guesses(self.pool, match, match.guesses[-1:], action_id)
            self._seat_submitted(match, seat)
            await self._publish(match, rec, change)
            await self._drive(match, rec)

    async def resign_match(
        self, match_id: str, session_key: str, action_id: str, expected_version: int
    ) -> None:
        async with await self.lock(match_id):
            match, rec = await self.load(match_id)
            if await action_seen(self.pool, match_id, action_id):
                return
            seat = await self._check_command(match, rec, session_key, "resign", expected_version)
            template = rec.template
            change = resign(match, seat, template)
            await store_turns(
                self.pool,
                match,
                TurnRow(
                    seat, "", "deterministic_invalid", layer1_result="resign", action_id=action_id
                ),
            )
            await self._publish(match, rec, change)
            await self._drive(match, rec)

    async def disagree(self, match_id: str, seq: int, session_key: str | None) -> None:
        """One vote per seated session and move, on a move the table can already see."""
        match, rec = await self.load(match_id)
        template = rec.template
        if session_key is None or await seat_of(self.pool, rec, session_key) is None:
            raise MatchError(403, "only a seat at this table can disagree")
        turn = next((t for t in match.turns if t.seq == seq), None)
        if turn is None or not visible_to(match, template, None, turn.round_n, turn.actor):
            raise MatchError(404, "no such turn")
        await add_disagreement(
            self.pool,
            match_id,
            seq,
            session_key,
            match.template_id,
            normalize(previous_of(match, template, turn)),
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
        """The requester's seat, when it may act now. Serial play checks the state version;
        hidden rounds are answered and called per seat, and say the round they were for."""
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
        # A move still with the judge holds the turn.
        if match.status != "active":
            raise MatchError(409, "not your move")
        if rec.row(seat).submitted:
            raise MatchError(409, "you already answered this round")
        if shows_live(template) and expected_version != match.state_version:
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

    # The turn pipeline

    async def _answer(
        self,
        match_id: str,
        template: Template,
        seat: str,
        round_n: int,
        action_id: str,
        text: str,
    ) -> None:
        """Rules a human seat's answer and lands it. Serial play holds the match lock from the
        judge call through the House's reply; a hidden round's answers are judged outside it."""
        try:
            if shows_live(template):
                async with await self.lock(match_id):
                    match, rec = await self.load(match_id)
                    if not _owes(match, template, seat, round_n):
                        return
                    ruled = await self._rule(match, template, text)
                    await self._land_answer(match, rec, seat, round_n, text, ruled, action_id)
                return
            match, _ = await self.load(match_id)
            ruled = await self._rule(match, template, text)
            async with await self.lock(match_id):
                match, rec = await self.load(match_id)
                await self._land_answer(match, rec, seat, round_n, text, ruled, action_id)
        except MatchClosed:
            log.info("match %s closed while waiting on the judge", match_id)
        except Exception:
            log.exception("answer on %s failed; the seat may answer again", match_id)
            await self._hand_back(match_id, seat)

    async def _land_answer(
        self,
        match: Match,
        rec: Record,
        seat: str,
        round_n: int,
        text: str,
        ruled: Judged | Layer1Reason,
        action_id: str,
    ) -> None:
        """Under the match lock: lands a human seat's ruled answer, unless the seat no longer owes
        it. An answer that comes back leaves the seat time to answer again."""
        template = rec.template
        await set_submitted(self.pool, match.id, seat, False)
        if not _owes(match, template, seat, round_n):
            return
        change = await self._land(match, rec, seat, text, ruled, action_id)
        if (change.strikes or change.repeat) and change.forfeit is None:
            await self._give_time(match, rec)
        await self._drive(match, rec)

    async def _hand_back(self, match_id: str, seat: str) -> None:
        """The seat's answer was lost on the way: it may answer again, with a whole clock if
        little was left."""
        async with await self.lock(match_id):
            await set_submitted(self.pool, match_id, seat, False)
            match, rec = await self.load(match_id)
            live = shows_live(rec.template)
            if live:
                await set_status(self.pool, match_id, "active")
                match.status = "active"
            deadline = await self._give_time(match, rec)
            if live:
                self._turn_changed(match, deadline)

    async def _rule(self, match: Match, template: Template, text: str) -> Judged | Layer1Reason:
        """Layer 1's refusal, or else the judge's ruling. Only serial play announces the judge
        and pauses through an outage."""
        reason = layer1(template, text, match)
        if reason is not None:
            return reason
        return await self.judge.rule(
            match,
            template,
            len(match.turns) + 1,
            text,
            judged_against(match, template),
            transcript(match, template, finished_only=True),
            card_in_play(match, template).hidden,
            quiet=not shows_live(template),
        )

    async def _land(
        self,
        match: Match,
        rec: Record,
        seat: str,
        text: str,
        ruled: Judged | Layer1Reason,
        action_id: str | None,
    ) -> Change:
        """Under the match lock: applies a ruled answer through the engine, stores it with the
        state it produced, and publishes what the table may see."""
        template = rec.template
        reason: str | None = None
        host, verdict_id = None, None
        if isinstance(ruled, Judged) and ruled.outcome != "semantic_reject":
            if not repeats_the_round(match, template, text):
                return await self._record(match, rec, seat, text, ruled, action_id)
            reason = "repeat"
            change = refuse_repeat(match, seat, template)
        elif isinstance(ruled, Judged):
            host, verdict_id = ruled.response.host, ruled.verdict_id
            change = apply_ruling(match, seat, text, "semantic_reject", template)
        else:
            reason = ruled
            change = apply_ruling(match, seat, text, "deterministic_invalid", template)
        rejected = rejection(match, template, seat, change.strikes, reason, host)
        refused = TurnRow(
            seat,
            text,
            rejected.outcome,
            layer1_result=reason,
            verdict_id=verdict_id,
            action_id=action_id,
            round_n=change.round_n,
        )
        await store_turns(self.pool, match, refused, *_forfeit_rows(change))
        await self._publish(match, rec, change, rejected=rejected)
        return change

    async def _record(
        self,
        match: Match,
        rec: Record,
        seat: str,
        text: str,
        judged: Judged,
        action_id: str | None,
    ) -> Change:
        template = rec.template
        response = judged.response
        hidden = card_in_play(match, template).hidden
        proximity = response.scoring.truth_proximity if hidden else "none"
        earned = weighted_total(response.scoring.scores, template.weights)
        change = apply_ruling(
            match, seat, text, judged.outcome, template, earned, truth_hit=proximity == "hit"
        )
        assert change.turn is not None
        await store_turns(
            self.pool,
            match,
            TurnRow(
                seat,
                text,
                judged.outcome,
                seq=change.turn.seq,
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
        host = response.host.model_copy(update={"badges": badges})
        await self._publish(match, rec, change, response=response.model_copy(update={"host": host}))
        return change

    async def _drive(self, match: Match, rec: Record) -> None:
        """Under the match lock: House seats that owe an answer give one, in serial play in turn
        until a human seat's clock restarts, in a hidden round at once on their own tasks."""
        template = rec.template
        if not shows_live(template):
            for seat in owed(match, template):
                if seat in rec.models:
                    self._start_writer(match.id, seat, match.round_n)
            return
        while (owing := owed(match, template)) and owing[0] in rec.models:
            await self._house_turn(match, rec, owing[0])
        if match.status != "active":
            return
        deadline = await self._set_clock(match, rec)
        self._turn_changed(match, deadline)
        mover = rec.row(match.to_move)
        if match.clocked and mover.session_key:
            await self.presence.send(
                mover.session_key, TurnNudge(match_id=match.id, title=template.title)
            )

    async def _give_time(self, match: Match, rec: Record) -> str | None:
        """A seat handed back its answer after the judge ran long gets a whole clock again.
        Returns the deadline in force."""
        left = rec.turn_deadline - datetime.now(UTC) if rec.turn_deadline else None
        playing = match.status in ("active", "awaiting_judgment")
        if match.clocked and playing and (left is None or left < GRACE):
            return await self._set_clock(match, rec)
        return rec.turn_deadline.isoformat() if rec.turn_deadline else None

    # House seats

    async def _house_turn(self, match: Match, rec: Record, seat: str) -> None:
        """Serial play, under the match lock: the House seat to move answers, or loses its turn
        when the judge gives no ruling."""
        template = rec.template
        try:
            written = await self._write_answer(match, rec, seat)
        except JudgeGaveUp:
            change = forfeit_turn(match, seat, template)
            await store_turns(self.pool, match, *_forfeit_rows(change))
            await self._publish(match, rec, change)
            return
        await hold_move(self.pool, match.id, seat, None, match.round_n)
        await self._play_house(match, rec, seat, written)

    def _start_writer(self, match_id: str, seat: str, round_n: int) -> None:
        key = (match_id, seat, round_n)
        if key in self.answering:
            return
        self.answering.add(key)
        self.spawn(self._house_answer(match_id, seat, round_n))

    async def _house_answer(self, match_id: str, seat: str, round_n: int) -> None:
        """A hidden round's House seat writes and is judged outside the match lock, then lands
        under it. An answer another seat already gave is written again, MODEL_REWRITES times."""
        try:
            for tries in range(MODEL_REWRITES + 1):
                match, rec = await self.load(match_id)
                template = rec.template
                if not _owes(match, template, seat, round_n):
                    return
                try:
                    written = await self._write_answer(match, rec, seat)
                except JudgeGaveUp:
                    written = None
                async with await self.lock(match_id):
                    match, rec = await self.load(match_id)
                    if not _owes(match, template, seat, round_n):
                        return
                    await hold_move(self.pool, match_id, seat, None, round_n)
                    # Two identical entries could not be told apart on the call.
                    if written and repeats_the_round(match, template, written[0]):
                        if tries < MODEL_REWRITES:
                            continue
                        written = None
                    await self._play_house(match, rec, seat, written)
                    await self._drive(match, rec)
                    return
        except MatchClosed:
            log.info("match %s closed while a House seat was writing", match_id)
        finally:
            self.answering.discard((match_id, seat, round_n))

    async def _write_answer(
        self, match: Match, rec: Record, seat: str
    ) -> tuple[str, Judged] | None:
        """The House seat's answer and its ruling, or None when it gives up after model_next's
        ladder. Only serial play lands each refusal, as a strike the table sees."""
        template = rec.template
        row = rec.row(seat)
        held = row.held_move if row.held_round == match.round_n else None
        text = held or await self._write(match, template, row)
        refusals = 0
        retold = False
        while True:
            ruled = await self._rule(match, template, text) if text is not None else None
            if isinstance(ruled, Judged):
                hit = ruled.response.scoring.truth_proximity == "hit"
                if hit and card_in_play(match, template).hidden and not retold:
                    # A House seat is meant to bluff: one more try when it wrote the truth.
                    retold = True
                    text = await self._write(match, template, row)
                    continue
                if ruled.outcome != "semantic_reject":
                    assert text is not None
                    return text, ruled
            if text is not None and ruled is not None and shows_live(template):
                await self._land(match, rec, seat, text, ruled, None)
            refusals += 1
            step = model_next(refusals, template)
            if step == "give_up":
                return None
            if step == "write":
                text = await self._write(match, template, row)
            else:
                text = template.default_move

    async def _write(self, match: Match, template: Template, row: SeatRow) -> str | None:
        """A House answer for the seat, held so a restart can reuse it; streamed to the table in
        serial play."""
        card = card_in_play(match, template)
        text = await self.house.write(match, template, row, card, silent=not shows_live(template))
        if not await match_is_live(self.pool, match.id):
            # The match closed during the write; the slot the write took goes back.
            self.house.release(match.id)
        if text is not None:
            await hold_move(self.pool, match.id, row.seat, text, match.round_n)
        return text

    async def _play_house(
        self,
        match: Match,
        rec: Record,
        seat: str,
        written: tuple[str, Judged] | None,
    ) -> None:
        """Under the match lock: lands the House seat's answer, or gives up its seat when it has
        none."""
        if written is not None:
            await self._land(match, rec, seat, *written, None)
            return
        change = resign(match, seat, rec.template)
        await save_match(self.pool, match)
        await self._publish(match, rec, change)

    # The clock

    async def expire_clocks(self) -> list[str]:
        """Forfeits every clocked turn past its deadline. Returns the matches it looked at."""
        overdue = await overdue_ids(self.pool)
        # Each on its own task: a House turn played after one forfeit holds up no other match.
        for match_id in overdue:
            if match_id not in self.expiring:
                self.expiring.add(match_id)
                self.spawn(self._expire(match_id))
        return overdue

    async def _expire(self, match_id: str) -> None:
        """A judge still working on an answer holds the clock."""
        try:
            async with await self.lock(match_id):
                match, rec = await self.load(match_id)
                now = datetime.now(UTC)
                deadline = rec.turn_deadline
                if match.status != "active" or deadline is None or deadline > now:
                    return
                await self._expire_turn(match, rec)
        finally:
            self.expiring.discard(match_id)

    async def _expire_turn(self, match: Match, rec: Record) -> None:
        """Every human seat that owes an answer or a call, and has not sent one, loses it."""
        template = rec.template
        phase = match.phase
        humans = match.human_seats
        late = [s for s in owed(match, template) if s in humans and not rec.row(s).submitted]
        phase_over = False
        for seat in late:
            if phase == "guess":
                change = skip_guess(match, seat, template)
                await store_guesses(self.pool, match, match.guesses[-1:], None)
            else:
                change = forfeit_turn(match, seat, template)
                await store_turns(self.pool, match, *_forfeit_rows(change))
            await self._publish(match, rec, change)
            phase_over = change.ended or change.round_closed or change.call_opened
            if phase_over:
                break
        if not shows_live(template) and phase == "write":
            if not phase_over:
                # Only answers still with the judge or a House seat remain; the round waits.
                await set_deadline(self.pool, match.id, None)
            self._turn_changed(match, None)
        await self._drive(match, rec)

    # Publishing

    async def _publish(
        self,
        match: Match,
        rec: Record,
        change: Change,
        rejected: TurnRejected | None = None,
        response: JudgeResponse | None = None,
    ) -> None:
        """Sends the table what it may see of one transition: the answer, a call that opened or
        a round that closed, then the end."""
        template = rec.template
        live = shows_live(template)
        coaching = None
        if rejected is not None:
            self._emit_rejection(match, template, rejected)
        if change.turn is not None and live and response is not None:
            self.bus.emit(match.id, _ruling(match, change.turn, response.scoring, response.host))
            if change.turn.outcome == "fail":
                coaching = response.host.coaching_line
        elif change.turn is not None and not live and change.turn.actor in match.human_seats:
            # A House seat's answer says nothing until the reveal.
            self._seat_submitted(match, change.turn.actor)
        if match.status == "active" and (change.call_opened or change.round_closed):
            await self._set_clock(match, rec)
        if change.call_opened:
            self.bus.emit(
                match.id,
                GuessOpened(round_n=match.round_n, state_version=match.state_version),
            )
        if change.round_closed:
            await self._reveal(match, template, change.round_n)
        if change.ended:
            await self._emit_match_ended(match, coaching)

    async def _reveal(self, match: Match, template: Template, round_n: int) -> None:
        """Sends a closed round's rulings, then its card's truth and the calls."""
        _, rec = await self.load(match.id)
        for row in rec.turn_rows:
            if row["round_n"] != round_n or row["scoring"] is None:
                continue
            scoring = ScoringPayload.model_validate(row["scoring"])
            host = HostPayload.model_validate(row["host"])
            turn = match.turns[row["seq"] - 1]
            self.bus.emit(match.id, _ruling(match, turn, scoring, host))
        card = template.seed_named(match.cards[round_n - 1])
        assert card is not None
        self.bus.emit(
            match.id,
            RoundRevealed(
                round_n=round_n,
                token=card.opening_token,
                emoji=card.opening_emoji,
                detail=card.detail,
                truth=card.hidden,
                guesses=guess_views(match, round_n),
                totals=dict(match.points),
                state_version=match.state_version,
            ),
        )

    def _emit_rejection(self, match: Match, template: Template, rejected: TurnRejected) -> None:
        """A refusal in a hidden round is about a hidden answer, so the stream says only whose it
        was; that seat reads the reason from its own snapshot."""
        if not shows_live(template):
            rejected = rejected.model_copy(update={"reason_text": "", "nudge_text": None})
        self.bus.emit(match.id, rejected)

    def _seat_submitted(self, match: Match, seat: str) -> None:
        self.bus.emit(
            match.id,
            SeatSubmitted(seat=seat, state_version=match.state_version),
        )

    def _turn_changed(self, match: Match, deadline: str | None) -> None:
        self.bus.emit(
            match.id,
            TurnChanged(
                to_move=match.to_move,
                turn_deadline=deadline,
                round_in_play=match.round_n,
                state_version=match.state_version,
            ),
        )

    async def _emit_match_ended(self, match: Match, coaching_line: str | None) -> None:
        snap = await self.snapshot(match.id)
        assert match.end_reason is not None
        self._forget(match.id)
        self.bus.emit(
            match.id,
            MatchEnded(
                end_reason=match.end_reason,
                result_kind=result_kind(match.end_reason, match.winner),
                winner=match.winner,
                totals={s.seat: s.points for s in snap.seats},
                highlight_seq=highlight_seq(snap),
                coaching_line=coaching_line,
                share_text=share_text(snap, self.public_base_url),
                replay_id=match.id,
                state_version=match.state_version,
            ),
        )


def _owes(match: Match, template: Template, seat: str, round_n: int) -> bool:
    """The seat still owes the answer it wrote for round_n."""
    return match.phase == "write" and match.round_n == round_n and seat in owed(match, template)


def _forfeit_rows(change: Change) -> list[TurnRow]:
    turn = change.forfeit
    if turn is None:
        return []
    return [TurnRow(turn.actor, "", "forfeit", seq=turn.seq, layer1_result="forfeit")]


def _ruling(match: Match, turn: Turn, scoring: ScoringPayload, host: HostPayload) -> Ruling:
    return Ruling(
        seq=turn.seq,
        round_n=turn.round_n,
        actor=turn.actor,
        move_text=turn.move_text,
        outcome=turn.outcome,
        scoring=scoring,
        host=host,
        points=turn.points or 0,
        totals=dict(match.points),
        to_move=match.to_move,
        round_in_play=match.round_n,
        state_version=match.state_version,
    )
