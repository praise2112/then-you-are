"""FastAPI application: HTTP, SSE, sessions, and the built frontend."""

import html
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from arena_core.template import load_template
from arena_judge.caller import ModelCaller
from arena_judge.schema import (
    JudgePaused,
    JudgeResumed,
    JudgeStarted,
    MatchEnded,
    MoveToken,
    Ruling,
    StateResync,
    TurnRejected,
)
from arena_server.config import Settings, load_model, load_settings
from arena_server.db import apply_schema, make_pool
from arena_server.events import EventBus
from arena_server.matches import MatchError, MatchService
from arena_server.views import MatchSnapshot, Replay, StageView, TemplateView

SESSION_COOKIE = "oddstage_session"


def build_app(settings: Settings | None = None, caller: ModelCaller | None = None) -> FastAPI:
    settings = settings or load_settings()
    template = load_template("then-i-am")
    judge_spec = load_model(settings.judge_ref)
    opponent_spec = load_model(settings.opponent_ref)
    caller = caller or ModelCaller(settings.openrouter_api_key, judge_spec, opponent_spec)
    pool = make_pool(settings.database_url)
    bus = EventBus()
    service = MatchService(
        pool,
        bus,
        caller,
        template,
        settings.opponent_ref,
        opponent_spec.display_name,
        judge_spec.model,
        settings.public_base_url,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        await apply_schema(pool)
        yield
        await caller.aclose()
        await pool.close()

    app = FastAPI(title="Oddstage", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.bus = bus

    @app.exception_handler(MatchError)
    async def match_error(_: Request, exc: MatchError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    class Health(BaseModel):
        status: str
        game: str

    class CreateMatch(BaseModel):
        template_id: str
        stage_name: str | None = Field(default=None, max_length=40)
        seed_token: str | None = None
        first_move: str | None = Field(default=None, max_length=2000)

    class MoveCommand(BaseModel):
        action_id: str = Field(min_length=1, max_length=64)
        expected_version: int
        move_text: str = Field(max_length=2000)

    class ResignCommand(BaseModel):
        action_id: str = Field(min_length=1, max_length=64)
        expected_version: int

    class Accepted(BaseModel):
        accepted: bool = True

    class SsePayloads(BaseModel):
        """Exported so the generated TypeScript client carries every event payload type."""

        turn_rejected: TurnRejected
        judge_started: JudgeStarted
        ruling: Ruling
        move_token: MoveToken
        judge_paused: JudgePaused
        judge_resumed: JudgeResumed
        match_ended: MatchEnded
        state_resync: StateResync

    def session_of(request: Request) -> str:
        key = request.cookies.get(SESSION_COOKIE)
        if not key:
            raise HTTPException(401, "no session")
        return key

    @app.get("/healthz")
    async def healthz() -> Health:
        return Health(status="ok", game=template.slug)

    @app.get("/templates/then-i-am")
    async def get_template() -> TemplateView:
        return TemplateView(**template.player_projection())

    @app.post("/matches", status_code=201)
    async def create_match(
        body: CreateMatch, request: Request, response: Response
    ) -> MatchSnapshot:
        if body.template_id != template.slug:
            raise HTTPException(404, "no such template")
        key = await service.ensure_session(request.cookies.get(SESSION_COOKIE), body.stage_name)
        response.set_cookie(
            SESSION_COOKIE, key, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365
        )
        snap = await service.create(key, body.seed_token)
        if body.first_move:
            await service.submit_move(snap.id, key, f"first-{snap.id}", 0, body.first_move)
            snap = await service.snapshot(snap.id)
        return snap

    @app.get("/matches/{match_id}")
    async def get_match(match_id: str) -> MatchSnapshot:
        return await service.snapshot(match_id)

    @app.post("/matches/{match_id}/moves", status_code=202)
    async def post_move(match_id: str, body: MoveCommand, request: Request) -> Accepted:
        await service.submit_move(
            match_id, session_of(request), body.action_id, body.expected_version, body.move_text
        )
        return Accepted()

    @app.post("/matches/{match_id}/resign", status_code=202)
    async def post_resign(match_id: str, body: ResignCommand, request: Request) -> Accepted:
        await service.resign_match(
            match_id, session_of(request), body.action_id, body.expected_version
        )
        return Accepted()

    @app.post("/matches/{match_id}/turns/{seq}/disagree", status_code=204)
    async def post_disagree(match_id: str, seq: int) -> Response:
        await service.disagree(match_id, seq)
        return Response(status_code=204)

    @app.get("/matches/{match_id}/events")
    async def events(
        match_id: str, last_event_id: str | None = Header(default=None)
    ) -> EventSourceResponse:
        await service.snapshot(match_id)
        last_id = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

        async def gen() -> AsyncIterator[dict]:
            async for event in bus.subscribe(match_id, last_id):
                yield {"id": str(event.id), "event": event.name, "data": event.data}

        return EventSourceResponse(gen())

    @app.get("/sse-payloads", include_in_schema=True)
    async def sse_payloads() -> SsePayloads:
        raise HTTPException(404, "schema-only endpoint")

    @app.get("/stage")
    async def get_stage() -> StageView:
        return await service.stage()

    @app.get("/replays/{match_id}")
    async def get_replay(match_id: str) -> Replay:
        return await service.replay(match_id)

    @app.get("/replays")
    async def list_replays(
        sort: Literal["curated", "newest", "longest"] = "curated",
    ) -> list[Replay]:
        return await service.replays(sort)

    class CurateCommand(BaseModel):
        curated: bool

    @app.post("/replays/{match_id}/curate", status_code=204)
    async def post_curate(
        match_id: str, body: CurateCommand, x_curator_token: str = Header(default="")
    ) -> Response:
        if not settings.curator_token or x_curator_token != settings.curator_token:
            raise HTTPException(403, "curator token required")
        await service.set_curated(match_id, body.curated)
        return Response(status_code=204)

    @app.get("/r/{match_id}", response_class=HTMLResponse)
    async def replay_shell(match_id: str) -> HTMLResponse:
        replay = await service.replay(match_id)
        title = html.escape(f"{replay.stage_name} vs {replay.opponent_name}, {template.title}")
        description = html.escape(replay.share_text)
        head = (
            f'<meta property="og:title" content="{title}">'
            f'<meta property="og:description" content="{description}">'
            f'<meta property="og:url" content="{settings.public_base_url}/r/{match_id}">'
        )
        if settings.frontend_dist:
            page = (settings.frontend_dist / "index.html").read_text()
            return HTMLResponse(page.replace("</head>", head + "</head>", 1))
        return HTMLResponse(
            f"<!doctype html><html><head>{head}<title>{title}</title></head>"
            f"<body>{description}</body></html>"
        )

    mockups = Path(__file__).parents[1] / "mockups"
    app.mount("/mockups", StaticFiles(directory=mockups, html=True), name="mockups")
    frontend_src = Path(__file__).parents[1] / "frontend"
    app.mount("/frontend", StaticFiles(directory=frontend_src), name="frontend-src")
    if settings.frontend_dist:
        mount_frontend(app, settings.frontend_dist)

    return app


def mount_frontend(app: FastAPI, dist: Path) -> None:
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


app = build_app()
