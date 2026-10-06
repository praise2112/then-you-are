"""Tables: creating a duel or a table, joining one, quick match, adding the House, and the lobby
of tables still filling."""

import asyncio
import secrets
from collections import defaultdict
from typing import TYPE_CHECKING, Literal

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from arena_core.state import deal
from arena_core.template import Template
from arena_server.db import Pool
from arena_server.events import MatchStarted, SeatJoined
from arena_server.presence import Lobby, Presence
from arena_server.sessions import lists_duels, open_match_ids, seat_of
from arena_server.store import (
    MatchError,
    insert_match,
    insert_seat,
    invite_match_id,
    joinable_ids,
    open_table_rows,
    start_table,
)
from arena_server.views import MatchSnapshot, TableKind, TableView

if TYPE_CHECKING:
    from arena_server.matches import MatchService


async def open_tables(pool: Pool, templates: dict[str, Template]) -> list[TableView]:
    rows = await open_table_rows(pool)
    return [
        TableView(
            id=r["id"],
            template_id=r["template_id"],
            title=templates[r["template_id"]].title,
            emblem=templates[r["template_id"]].emblem,
            host_name=r["host_name"] or "Challenger",
            invite_code=r["invite_code"],
            seats_taken=r["taken"],
            seats_wanted=r["seats_wanted"],
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
        if r["template_id"] in templates
    ]


async def broadcast_lobby(presence: Presence, pool: Pool, templates: dict[str, Template]) -> None:
    await presence.broadcast(Lobby(tables=await open_tables(pool, templates)))


class Tables:
    def __init__(self, service: "MatchService"):
        self.service = service
        # Quick match searches and creates under one lock per game.
        self.seating: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def create(
        self,
        session_key: str,
        template_id: str,
        seed_token: str | None = None,
        kind: TableKind = "house",
        seats: int = 2,
    ) -> MatchSnapshot:
        """A House duel starts at once. A table for friends or for anyone waits for its seats;
        its creator holds the first one."""
        service = self.service
        template = service.templates.get(template_id)
        if template is None:
            raise MatchError(404, "no such template")
        first = template.seed_named(seed_token) if seed_token else None
        if seed_token and first is None:
            raise MatchError(422, "that opening is not in this game")
        if kind == "house":
            seats = 2
        elif not template.num_players.min <= seats <= template.num_players.max:
            raise MatchError(
                422,
                f"{template.title} seats {template.num_players.min} to "
                f"{template.num_players.max} players",
            )
        open_ids = await open_match_ids(service.pool, session_key, template.slug, kind)
        if open_ids:
            return await service.snapshot(open_ids[0], session_key)
        cards = deal(template, secrets.SystemRandom(), first, template.revealed_card)
        match_id = secrets.token_urlsafe(8)
        listed = await lists_duels(service.pool, session_key)
        house = kind == "house"
        async with service.pool.connection() as conn, conn.transaction():
            await insert_match(
                conn,
                match_id,
                template,
                cards,
                "active" if house else "open",
                listed,
                kind,
                seats,
                None if house else secrets.token_urlsafe(6),
            )
            await self._insert_seat(conn, match_id, "p1", "human", session_key)
            if house:
                await self._insert_seat(conn, match_id, "p2", "model", None)
        if house:
            await service.resume(match_id)
        else:
            await self._broadcast_lobby()
        return await service.snapshot(match_id, session_key)

    async def join(self, invite_code: str, session_key: str) -> str:
        """Seats the session at the table behind an invite code. Returns the match id."""
        match_id = await invite_match_id(self.service.pool, invite_code)
        if match_id is None:
            raise MatchError(404, "no table with that invite")
        await self._fill_seat(match_id, session_key, "human")
        return match_id

    async def quick_match(self, session_key: str, template_id: str, seats: int) -> str:
        """Takes a seat at the oldest open table for the game, or opens one. Returns its id."""
        pool = self.service.pool
        if template_id not in self.service.templates:
            raise MatchError(404, "no such template")
        async with self.seating[template_id]:
            mine = await open_match_ids(pool, session_key, template_id, "open")
            if mine:
                return mine[0]
            for match_id in await joinable_ids(pool, template_id, session_key):
                try:
                    await self._fill_seat(match_id, session_key, "human")
                except MatchError as e:
                    # Filled or closed by an invite or the House since it was listed.
                    if e.status != 409:
                        raise
                    continue
                return match_id
            snap = await self.create(session_key, template_id, kind="open", seats=seats)
            return snap.id

    async def add_house(self, match_id: str, session_key: str) -> None:
        """The table's creator fills the next open seat with the House."""
        await self._fill_seat(match_id, session_key, "model")

    async def _insert_seat(
        self,
        conn: AsyncConnection[DictRow],
        match_id: str,
        seat: str,
        kind: str,
        session_key: str | None,
    ) -> None:
        model_ref = self.service.opponent_ref if kind == "model" else None
        await insert_seat(conn, match_id, seat, kind, session_key, model_ref)
        if kind == "model":
            self.service.spawn(self.service.caller.wake_opponent())

    async def _fill_seat(
        self, match_id: str, session_key: str, kind: Literal["human", "model"]
    ) -> None:
        """Seats the session, or for its creator the House, at the next seat of a filling table.
        A session already seated is left where it is."""
        service = self.service
        async with service.locks[match_id]:
            match, rec = await service.load(match_id)
            seated = await seat_of(service.pool, rec, session_key)
            if kind == "human" and seated:
                return
            if kind == "model" and seated != rec.owner.seat:
                raise MatchError(403, "only the table's creator can add the House")
            if match.status != "open":
                raise MatchError(409, "this table has already started")
            seat = f"p{len(rec.seats) + 1}"
            async with service.pool.connection() as conn:
                await self._insert_seat(
                    conn, match_id, seat, kind, session_key if kind == "human" else None
                )
            await self._seated(match_id, seat)

    async def _seated(self, match_id: str, seat: str) -> None:
        """A seat was filled; the table starts when it is full. Runs under the match lock."""
        service = self.service
        _, rec = await service.load(match_id)
        service.bus.emit(match_id, "seat_joined", SeatJoined(seat=seat, state_version=0))
        if len(rec.seats) >= rec.seats_wanted:
            await start_table(service.pool, match_id)
            match, rec = await service.load(match_id)
            service.bus.emit(match_id, "match_started", MatchStarted(state_version=0))
            await service.start(match, rec)
        service.spawn(self._broadcast_lobby())

    async def _broadcast_lobby(self) -> None:
        service = self.service
        await broadcast_lobby(service.presence, service.pool, service.templates)
