"""Per-match event buffer and the SSE payloads it carries. Event ids are "<generation>-<n>", so a
Last-Event-ID from an earlier process replays the buffer from the start."""

import asyncio
import secrets
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from arena_core.state import EndReason, Outcome, ResultKind
from arena_judge.schema import HostPayload, ScoringPayload


@dataclass
class Event:
    id: str
    name: str
    data: str


@dataclass
class MatchStream:
    events: list[Event] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    # How many events lead up to the last one that changed the match.
    settled: int = 0


class EventBus:
    def __init__(self) -> None:
        self.generation = secrets.token_hex(3)
        self.streams: dict[str, MatchStream] = defaultdict(MatchStream)

    def emit(self, match_id: str, name: str, payload: BaseModel) -> None:
        stream = self.streams[match_id]
        event_id = f"{self.generation}-{len(stream.events) + 1}"
        stream.events.append(Event(id=event_id, name=name, data=payload.model_dump_json()))
        if "state_version" in type(payload).model_fields:
            stream.settled = len(stream.events)
        stream.changed.set()
        stream.changed = asyncio.Event()

    def cursor(self, match_id: str) -> str:
        """Where a snapshot's stream starts: after the match's last change, so the events of a
        ruling still under way are sent in full."""
        stream = self.streams.get(match_id)
        return f"{self.generation}-{stream.settled if stream else 0}"

    def cursor_from(self, last_event_id: str | None) -> int:
        """How many events the client already has; a cursor from another generation is zero."""
        generation, _, n = (last_event_id or "").rpartition("-")
        return int(n) if generation == self.generation and n.isdigit() else 0

    def forget(self, match_id: str) -> None:
        self.streams.pop(match_id, None)

    async def subscribe(self, match_id: str, last_id: int = 0) -> AsyncIterator[Event]:
        """The match's events after the first `last_id`. Only for a match still in play."""
        stream = self.streams[match_id]
        cursor = last_id
        while True:
            while cursor < len(stream.events):
                cursor += 1
                yield stream.events[cursor - 1]
            await stream.changed.wait()


class TurnRejected(BaseModel):
    # The seat whose move came back; other clients ignore it.
    seat: str
    outcome: Literal["deterministic_invalid", "semantic_reject"]
    reason_text: str
    strikes: int
    state_version: int
    nudge_text: str | None = None


class JudgeStarted(BaseModel):
    seq: int


class Ruling(BaseModel):
    seq: int
    round_n: int
    actor: str
    move_text: str
    outcome: Outcome
    scoring: ScoringPayload
    host: HostPayload
    points: int
    # Every seat's total once this ruling lands.
    totals: dict[str, int]
    to_move: str
    # The round the match is in once this ruling lands; past the budget when it ended.
    round_in_play: int
    state_version: int


class MoveToken(BaseModel):
    seq: int
    text: str


class JudgePaused(BaseModel):
    seq: int
    host_text: str
    move_text: str


class JudgeResumed(BaseModel):
    seq: int


class GuessOption(BaseModel):
    """One entry on the table during a call. The key says nothing about which is real."""

    key: str
    text: str


class GuessView(BaseModel):
    """A call made: who picked, what they picked (the truth or a player's bluff), who got paid."""

    actor: str
    picked: str
    points: int
    awarded_to: str


class GuessOpened(BaseModel):
    """Showcase only: every answer is judged and the guessers may call the real entry. Each
    guesser reads its own options from the snapshot."""

    round_n: int
    state_version: int


class RoundRevealed(BaseModel):
    """Showcase only: every call is in, so the card's truth may be shown."""

    round_n: int
    token: str
    emoji: str
    detail: str
    truth: str
    guesses: list[GuessView]
    totals: dict[str, int]
    state_version: int


class MatchEnded(BaseModel):
    end_reason: EndReason
    result_kind: ResultKind
    winner: str | None
    totals: dict[str, int]
    highlight_seq: int | None
    coaching_line: str | None = None
    share_text: str
    replay_id: str
    state_version: int


class SeatJoined(BaseModel):
    """A player took a seat at a table that is still filling."""

    seat: str
    state_version: int


class MatchStarted(BaseModel):
    """Every seat is filled and play begins."""

    state_version: int


class SeatSubmitted(BaseModel):
    """Showcase: a seat has written or called for the round in play. Says nothing about what."""

    seat: str
    state_version: int


class TurnChanged(BaseModel):
    """The turn passed without a ruling to show: a forfeit, a resign, or play moved on."""

    to_move: str
    turn_deadline: str | None
    round_in_play: int
    state_version: int
