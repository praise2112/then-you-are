import asyncio
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from arena_server.app import mount_frontend
from arena_server.events import EventBus


class Note(BaseModel):
    n: int


def test_event_ids_carry_the_process_generation_so_an_old_cursor_replays_from_the_start():
    bus = EventBus()
    bus.emit("m", "ruling", Note(n=1))
    bus.emit("m", "ruling", Note(n=2))
    first, second = bus.streams["m"].events
    assert first.id == f"{bus.generation}-1" and second.id == f"{bus.generation}-2"
    assert bus.cursor_from(second.id) == 2
    assert bus.cursor_from("deadbe-2") == 0 and bus.cursor_from(None) == 0

    async def first_after(cursor: int) -> str:
        async for event in bus.subscribe("m", cursor):
            return event.data
        raise AssertionError("no event")

    assert asyncio.run(first_after(bus.cursor_from(first.id))) == '{"n":2}'
    assert asyncio.run(first_after(bus.cursor_from("deadbe-2"))) == '{"n":1}'
    bus.forget("m")
    assert "m" not in bus.streams


class Change(BaseModel):
    state_version: int


def test_a_snapshot_cursor_stops_at_the_last_change_so_a_ruling_under_way_is_sent_whole():
    bus = EventBus()
    assert bus.cursor("m") == f"{bus.generation}-0" and "m" not in bus.streams
    bus.emit("m", "ruling", Change(state_version=1))
    bus.emit("m", "judge_started", Note(n=2))
    bus.emit("m", "move_token", Note(n=2))
    assert bus.cursor("m") == f"{bus.generation}-1"
    bus.emit("m", "turn_changed", Change(state_version=2))
    assert bus.cursor("m") == f"{bus.generation}-4"


def test_the_frontend_fallback_never_serves_a_file_outside_the_bundle(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>shell</html>")
    (dist / "app.js").write_text("app")
    (tmp_path / "secret.txt").write_text("secret")
    app = FastAPI()
    mount_frontend(app, dist)

    async def get(path: str) -> str:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return (await client.get(path)).text

    assert asyncio.run(get("/app.js")) == "app"
    assert asyncio.run(get("/" + quote("../secret.txt", safe=""))) == "<html>shell</html>"
    assert asyncio.run(get("/deep/route")) == "<html>shell</html>"
