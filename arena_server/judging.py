"""The judge: retries one judge call until it rules, pausing the match while the judge is out."""

import asyncio
import logging
import time
from dataclasses import dataclass

from arena_core.state import Match, Outcome
from arena_core.template import Template
from arena_judge.caller import ModelCaller
from arena_judge.schema import JudgeResponse, route_outcome
from arena_server.db import Pool
from arena_server.events import EventBus, JudgePaused, JudgeResumed, JudgeStarted
from arena_server.store import MatchClosed, insert_verdict, set_status, status_of

PAUSE_BACKOFF_S = (5, 10, 20, 30)
# A judge call refused for credentials or credit will not heal on its own; retry slowly.
BILLING_STATUSES = (401, 402, 403)
BILLING_RETRY_S = 60
# A move whose judge has not ruled by then goes back: a human may play again, the House loses it.
JUDGE_GIVE_UP_S = 120

log = logging.getLogger(__name__)


class JudgeGaveUp(Exception):
    """The judge gave no ruling within JUDGE_GIVE_UP_S."""


@dataclass
class Judged:
    outcome: Outcome
    response: JudgeResponse
    verdict_id: int


class Judge:
    def __init__(self, pool: Pool, bus: EventBus, caller: ModelCaller, judge_model: str):
        self.pool = pool
        self.bus = bus
        self.caller = caller
        self.judge_model = judge_model
        # Why the judge provider last refused a call for credentials or credit; None once it rules.
        self.fault: str | None = None

    async def rule(
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
        """Retries the judge call until it rules; unless quiet, announces it and pauses through
        an outage. Raises MatchClosed if the match closes, JudgeGaveUp after JUDGE_GIVE_UP_S."""
        if not quiet:
            self.bus.emit(match.id, "judge_started", JudgeStarted(seq=seq))
        paused = False
        attempt = 0
        started = time.monotonic()
        while True:
            call = await self.caller.judge(template, transcript, previous, move_text, hidden)
            verdict_id = await insert_verdict(self.pool, call, self.judge_model)
            if call.response is not None:
                self.fault = None
                if paused:
                    await set_status(self.pool, match.id, "awaiting_judgment")
                    self.bus.emit(match.id, "judge_resumed", JudgeResumed(seq=seq))
                return Judged(route_outcome(call.response.scoring), call.response, verdict_id)
            if not paused and not quiet:
                paused = True
                await set_status(self.pool, match.id, "paused")
                host_text = template.judge_out_text.strip().format(standing_form=previous)
                self.bus.emit(
                    match.id,
                    "judge_paused",
                    JudgePaused(seq=seq, host_text=host_text, move_text=move_text),
                )
            if call.error_status in BILLING_STATUSES:
                fault = f"judge provider answered HTTP {call.error_status}"
                if self.fault != fault:
                    log.error("%s; retrying every %s s", fault, BILLING_RETRY_S)
                self.fault = fault
                await asyncio.sleep(BILLING_RETRY_S)
            else:
                await asyncio.sleep(PAUSE_BACKOFF_S[min(attempt, len(PAUSE_BACKOFF_S) - 1)])
            attempt += 1
            if await status_of(self.pool, match.id) in ("abandoned", "ended"):
                raise MatchClosed(match.id)
            if time.monotonic() - started > JUDGE_GIVE_UP_S:
                raise JudgeGaveUp(match.id)
