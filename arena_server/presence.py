"""Who has the site open, by session, and pushing small messages to them over a WebSocket.
The registry lives in this one process."""

import logging
from collections import defaultdict
from typing import Literal

from fastapi import WebSocket
from pydantic import BaseModel

from arena_server.views import TableView

log = logging.getLogger(__name__)


class Online(BaseModel):
    type: Literal["online"] = "online"
    count: int


class Lobby(BaseModel):
    type: Literal["lobby"] = "lobby"
    tables: list[TableView]


class TurnNudge(BaseModel):
    """Your seat is to move in a match you are not looking at."""

    type: Literal["turn_nudge"] = "turn_nudge"
    match_id: str
    title: str


class Presence:
    def __init__(self) -> None:
        self.sockets: dict[str, set[WebSocket]] = defaultdict(set)

    def add(self, session_key: str, socket: WebSocket) -> None:
        self.sockets[session_key].add(socket)

    def remove(self, session_key: str, socket: WebSocket) -> None:
        self.sockets[session_key].discard(socket)
        if not self.sockets[session_key]:
            del self.sockets[session_key]

    @property
    def count(self) -> int:
        return len(self.sockets)

    async def send(self, session_key: str, message: BaseModel) -> None:
        text = message.model_dump_json()
        for socket in list(self.sockets.get(session_key, ())):
            await self._send(socket, text)

    async def broadcast(self, message: BaseModel) -> None:
        text = message.model_dump_json()
        for sockets in list(self.sockets.values()):
            for socket in list(sockets):
                await self._send(socket, text)

    async def _send(self, socket: WebSocket, text: str) -> None:
        try:
            await socket.send_text(text)
        except Exception:
            # A socket that closed mid-send is dropped when its receive loop ends.
            log.debug("dropped a message to a closing socket")
