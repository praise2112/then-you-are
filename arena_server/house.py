"""The House: writes a model seat's move, pinned to a llama-server slot, and hands the seat to
the stand-in model once the House fails."""

import asyncio
import logging
from collections.abc import AsyncIterator

from arena_core.state import Match, transcript
from arena_core.template import Template
from arena_judge.caller import CallError, ModelCaller, ModelSpec
from arena_judge.prompt import clean_move
from arena_server.db import Pool
from arena_server.events import EventBus, MoveToken
from arena_server.store import seat_model_ref, set_model_ref

# A House call that fails is tried once more before the fallback takes the seat.
HOUSE_ATTEMPTS = 2
HOUSE_RETRY_S = 3

log = logging.getLogger(__name__)


class House:
    def __init__(
        self,
        pool: Pool,
        bus: EventBus,
        caller: ModelCaller,
        house_slots: int,
        fallback: tuple[str, ModelSpec] | None,
    ):
        self.pool = pool
        self.bus = bus
        self.caller = caller
        self.house_slots = house_slots
        # The hosted model that takes a House seat for the rest of a match once the House fails.
        self.fallback = fallback
        # The llama-server slot each House seat's conversation is pinned to: (match, seat).
        self.slots: dict[tuple[str, str], int] = {}

    async def write(
        self,
        match: Match,
        template: Template,
        seat: str,
        prompt: str,
        hidden: str = "",
        silent: bool = False,
    ) -> str:
        """The seat's next move, streamed to the table unless silent; "" when every call
        failed."""
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

    def stand_in(self, model_ref: str | None) -> str | None:
        """The fallback model's name for a move it played in the House's place."""
        if self.fallback is None or model_ref != self.fallback[0]:
            return None
        return self.fallback[1].display_name

    def release(self, match_id: str) -> None:
        """Frees the slots the match's House seats held."""
        for key in [k for k in self.slots if k[0] == match_id]:
            del self.slots[key]

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
        return await seat_model_ref(self.pool, match_id, seat) == self.fallback[0]

    async def _stand_in_for(self, match_id: str, seat: str) -> None:
        assert self.fallback is not None
        await set_model_ref(self.pool, match_id, seat, self.fallback[0])
