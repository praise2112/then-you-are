"""The House: writes a model seat's move, pinned to a llama-server slot, and hands the seat to
the stand-in model once the House fails."""

import asyncio
import logging
from collections.abc import AsyncIterator

from arena_core.state import Match, fallen_lines, transcript
from arena_core.template import Seed, Template
from arena_judge.caller import CallError, ModelCaller, ModelSpec
from arena_judge.prompt import clean_move
from arena_server.db import Pool
from arena_server.events import EventBus, MoveToken
from arena_server.store import SeatRow, set_model_ref

# A House call that fails is tried once more before the stand-in takes the seat.
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
        stand_in: tuple[str, ModelSpec] | None,
    ):
        self.pool = pool
        self.bus = bus
        self.caller = caller
        self.house_slots = house_slots
        # The hosted model, by ref and spec, that takes a House seat for the rest of a match
        # once the House fails.
        self.stand_in = stand_in
        # The llama-server slot each House seat's conversation is pinned to: (match, seat).
        self.slots: dict[tuple[str, str], int] = {}

    async def write(
        self, match: Match, template: Template, row: SeatRow, card: Seed, silent: bool
    ) -> str | None:
        """The seat's answer to the card, streamed to the table unless silent; None when every
        call failed. A stand-in taking the seat is recorded on `row` as well."""
        lines = transcript(match, template, finished_only=True)
        fell = fallen_lines(match, template)
        prompt, hidden = card.card_text, card.hidden
        if self.stand_in is None or row.model_ref != self.stand_in[0]:
            for attempt in range(HOUSE_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(HOUSE_RETRY_S)
                stream = self.caller.opponent_stream(
                    template,
                    row.seat,
                    prompt,
                    lines,
                    hidden,
                    self._slot(match.id, row.seat),
                    fell=fell,
                )
                try:
                    return await self._collect_move(match, stream, silent)
                except CallError as e:
                    log.warning("opponent call failed for %s: %s", match.id, e)
            if self.stand_in is None:
                return None
            await set_model_ref(self.pool, match.id, row.seat, self.stand_in[0])
            row.model_ref = self.stand_in[0]
        stream = self.caller.opponent_stream(
            template, row.seat, prompt, lines, hidden, spec=self.stand_in[1], fell=fell
        )
        try:
            return await self._collect_move(match, stream, silent)
        except CallError as e:
            log.warning("stand-in call failed for %s: %s", match.id, e)
            return None

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
                self.bus.emit(match.id, MoveToken(seq=seq, text=chunk))
        return clean_move("".join(parts))
