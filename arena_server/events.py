"""Per-match event buffer. Ids are sequential so a client can resume from Last-Event-ID."""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class Event:
    id: int
    name: str
    data: str


@dataclass
class MatchStream:
    events: list[Event] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class EventBus:
    def __init__(self) -> None:
        self.streams: dict[str, MatchStream] = defaultdict(MatchStream)

    def emit(self, match_id: str, name: str, payload: BaseModel) -> None:
        stream = self.streams[match_id]
        stream.events.append(
            Event(id=len(stream.events) + 1, name=name, data=payload.model_dump_json())
        )
        stream.changed.set()
        stream.changed = asyncio.Event()

    async def subscribe(self, match_id: str, last_id: int = 0) -> AsyncIterator[Event]:
        stream = self.streams[match_id]
        cursor = last_id
        while True:
            while cursor < len(stream.events):
                cursor += 1
                yield stream.events[cursor - 1]
            waiter = stream.changed
            try:
                await asyncio.wait_for(waiter.wait(), timeout=15)
            except TimeoutError:
                yield Event(id=cursor, name="ping", data="{}")
