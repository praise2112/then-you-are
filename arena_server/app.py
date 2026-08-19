"""FastAPI application. Phase 1 skeleton: health plus the SSE transport shape."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="Oddstage", version="0.1.0")


class Health(BaseModel):
    status: str
    game: str


@app.get("/healthz")
async def healthz() -> Health:
    return Health(status="ok", game="then-i-am")


async def _demo_events() -> AsyncIterator[dict[str, str]]:
    for name in ("judge_started", "ruling", "match_ended"):
        await asyncio.sleep(0.05)
        yield {"event": name, "data": json.dumps({"placeholder": True})}


@app.get("/matches/{match_id}/stream")
async def stream(match_id: str) -> EventSourceResponse:
    return EventSourceResponse(_demo_events())
