"""Per-match event buffer. Ids are "<generation>-<n>": sequential within a process so a client
resumes from Last-Event-ID, and stamped with the process generation so a cursor from before a
restart replays the new buffer from the start."""

import asyncio
import secrets
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class Event:
    id: str
    name: str
    data: str


@dataclass
class MatchStream:
    events: list[Event] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class EventBus:
    def __init__(self) -> None:
        self.generation = secrets.token_hex(3)
        self.streams: dict[str, MatchStream] = defaultdict(MatchStream)

    def emit(self, match_id: str, name: str, payload: BaseModel) -> None:
        stream = self.streams[match_id]
        event_id = f"{self.generation}-{len(stream.events) + 1}"
        stream.events.append(Event(id=event_id, name=name, data=payload.model_dump_json()))
        stream.changed.set()
        stream.changed = asyncio.Event()

    def cursor_from(self, last_event_id: str | None) -> int:
        """How many events the client already has; a cursor from another generation is zero."""
        generation, _, n = (last_event_id or "").rpartition("-")
        return int(n) if generation == self.generation and n.isdigit() else 0

    def forget(self, match_id: str) -> None:
        self.streams.pop(match_id, None)

    async def subscribe(self, match_id: str, last_id: int = 0) -> AsyncIterator[Event]:
        stream = self.streams[match_id]
        cursor = last_id
        while True:
            while cursor < len(stream.events):
                cursor += 1
                yield stream.events[cursor - 1]
            await stream.changed.wait()
