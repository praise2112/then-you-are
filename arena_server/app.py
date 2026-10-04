"""FastAPI application: HTTP, SSE, sessions, and the built frontend."""

import asyncio
import html
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketDisconnect
from langfuse import Langfuse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.sessions import SessionMiddleware

from arena_core.template import Template, load_templates
from arena_judge.caller import ModelCaller
from arena_judge.schema import (
    GuessOpened,
    JudgePaused,
    JudgeResumed,
    JudgeStarted,
    MatchEnded,
    MatchStarted,
    MoveToken,
    RoundRevealed,
    Ruling,
    SeatJoined,
    SeatSubmitted,
    StateResync,
    TurnChanged,
    TurnRejected,
)
from arena_server.auth import (
    SESSION_COOKIE,
    DropRevokedSession,
    mount_auth,
    rename_account,
    set_session_cookie,
)
from arena_server.config import Settings, load_model, load_settings
from arena_server.db import apply_schema, make_pool
from arena_server.events import EventBus
from arena_server.leaderboard import board, boards_index
from arena_server.matches import MatchError, MatchService
from arena_server.names import check_name
from arena_server.presence import Lobby, Online, Presence, TurnNudge
from arena_server.profiles import profile
from arena_server.views import (
    BoardSummary,
    BoardView,
    MatchSnapshot,
    ProfileView,
    Replay,
    SessionView,
    StageView,
    TableView,
    TemplateView,
)

SWEEP_EVERY_S = 60
CLOCK_EVERY_S = 10
FALLBACK_REF = "opponent-v1"
log = logging.getLogger(__name__)


def build_app(settings: Settings | None = None, caller: ModelCaller | None = None) -> FastAPI:
    settings = settings or load_settings()
    templates = load_templates()
    if settings.featured_template not in templates:
        raise KeyError(
            f"FEATURED_TEMPLATE {settings.featured_template!r} is not a template on disk"
        )
    # The featured game leads the list; the landing opens on it.
    templates = {
        settings.featured_template: templates[settings.featured_template],
        **templates,
    }
    judge_spec = load_model(settings.judge_ref)
    opponent_spec = load_model(settings.opponent_ref)
    for spec in (judge_spec, opponent_spec):
        if spec.api_key_env and not os.environ.get(spec.api_key_env):
            raise RuntimeError(f"{spec.api_key_env} is not set")
    caller = caller or ModelCaller(
        settings.openrouter_api_key,
        judge_spec,
        opponent_spec,
        tracer=Langfuse() if settings.trace_calls else None,
    )
    pool = make_pool(settings.database_url)
    bus = EventBus()
    presence = Presence()
    service = MatchService(
        pool,
        bus,
        caller,
        templates,
        settings.opponent_ref,
        opponent_spec.display_name,
        judge_spec.model,
        settings.public_base_url,
        presence,
        opponent_spec.slots or 0,
        (FALLBACK_REF, load_model(FALLBACK_REF)) if opponent_spec.base_url else None,
    )

    async def sweep_abandoned() -> None:
        while True:
            await asyncio.sleep(SWEEP_EVERY_S)
            try:
                closed = await service.close_abandoned()
            except Exception:
                log.exception("abandon sweep failed")
                continue
            if closed:
                log.info("abandoned %d idle matches", len(closed))

    async def sweep_clocks() -> None:
        while True:
            await asyncio.sleep(CLOCK_EVERY_S)
            try:
                await service.expire_clocks()
            except Exception:
                log.exception("clock sweep failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        await apply_schema(pool)
        await service.recover()
        sweepers = [asyncio.create_task(sweep_abandoned()), asyncio.create_task(sweep_clocks())]
        yield
        for sweeper in sweepers:
            sweeper.cancel()
        await caller.aclose()
        await pool.close()

    app = FastAPI(title="Then You Are", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.add_middleware(DropRevokedSession, pool=pool)
    app.state.service = service
    app.state.bus = bus
    mount_auth(app, settings, pool)

    @app.exception_handler(MatchError)
    async def match_error(_: Request, exc: MatchError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    class Health(BaseModel):
        status: str
        games: list[str]
        judge: str

    class CreateMatch(BaseModel):
        template_id: str
        stage_name: str | None = Field(default=None, max_length=40)
        seed_token: str | None = None
        first_move: str | None = Field(default=None, max_length=2000)
        kind: Literal["house", "friends"] = "house"
        seats: int = Field(default=2, ge=2, le=6)

    class JoinTable(BaseModel):
        invite_code: str = Field(min_length=1, max_length=16)
        stage_name: str | None = Field(default=None, max_length=40)

    class QuickMatch(BaseModel):
        template_id: str
        seats: int = Field(default=2, ge=2, le=6)
        stage_name: str | None = Field(default=None, max_length=40)

    class Seated(BaseModel):
        match_id: str

    class MoveCommand(BaseModel):
        action_id: str = Field(min_length=1, max_length=64)
        expected_version: int
        move_text: str = Field(max_length=2000)
        # Showcase: the round this answer was written for.
        round_n: int | None = None

    class VisibilityCommand(BaseModel):
        public: bool

    class SessionUpdate(BaseModel):
        stage_name: str | None = Field(default=None, max_length=40)
        list_duels: bool | None = None

    class ResignCommand(BaseModel):
        action_id: str = Field(min_length=1, max_length=64)
        expected_version: int

    class GuessCommand(BaseModel):
        action_id: str = Field(min_length=1, max_length=64)
        expected_version: int
        key: str = Field(min_length=1, max_length=16)
        round_n: int | None = None

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
        round_revealed: RoundRevealed
        guess_opened: GuessOpened
        state_resync: StateResync
        seat_joined: SeatJoined
        match_started: MatchStarted
        seat_submitted: SeatSubmitted
        turn_changed: TurnChanged

    class WsPayloads(BaseModel):
        """Exported so the generated TypeScript client carries every socket message type."""

        online: Online
        lobby: Lobby
        turn_nudge: TurnNudge

    def refuse_bad_name(stage_name: str | None) -> None:
        if stage_name and (refusal := check_name(stage_name)):
            raise HTTPException(422, refusal)

    async def player_session(request: Request, response: Response, stage_name: str | None) -> str:
        """The caller's session, made on first play and renamed when a stage name comes along."""
        refuse_bad_name(stage_name)
        key = await service.ensure_session(request.cookies.get(SESSION_COOKIE), stage_name)
        set_session_cookie(response, key, settings.secure_cookies)
        return key

    def session_of(request: Request) -> str:
        key = request.cookies.get(SESSION_COOKIE)
        if not key:
            raise HTTPException(401, "no session")
        return key

    @app.get("/healthz")
    async def healthz() -> Health:
        return Health(status="ok", games=list(templates), judge=service.judge_fault or "ok")

    def template_view(template: Template) -> TemplateView:
        featured = template.slug == settings.featured_template
        return TemplateView(**template.player_projection(), featured=featured)

    @app.get("/templates")
    async def list_templates() -> list[TemplateView]:
        return [template_view(t) for t in templates.values()]

    @app.get("/templates/{slug}")
    async def get_template(slug: str) -> TemplateView:
        if slug not in templates:
            raise HTTPException(404, "no such template")
        return template_view(templates[slug])

    async def session_with_providers(key: str | None) -> SessionView:
        view = await service.session_view(key)
        view.providers = list(settings.oauth_clients)
        return view

    @app.get("/sessions/me")
    async def get_session(request: Request) -> SessionView:
        return await session_with_providers(request.cookies.get(SESSION_COOKIE))

    @app.get("/leaderboard")
    async def get_boards() -> list[BoardSummary]:
        return await boards_index(pool, templates)

    @app.get("/leaderboard/{slug}")
    async def get_board(slug: str) -> BoardView:
        if slug not in templates:
            raise HTTPException(404, "no such game")
        return await board(pool, templates[slug])

    @app.put("/sessions/me")
    async def put_session(body: SessionUpdate, request: Request, response: Response) -> SessionView:
        refuse_bad_name(body.stage_name)
        key = await service.ensure_session(
            request.cookies.get(SESSION_COOKIE), body.stage_name, body.list_duels
        )
        if body.stage_name and body.stage_name.strip():
            await rename_account(pool, key, body.stage_name.strip()[:40])
        set_session_cookie(response, key, settings.secure_cookies)
        return await session_with_providers(key)

    @app.post("/matches", status_code=201)
    async def create_match(
        body: CreateMatch, request: Request, response: Response
    ) -> MatchSnapshot:
        if body.template_id not in templates:
            raise HTTPException(404, "no such template")
        key = await player_session(request, response, body.stage_name)
        snap = await service.create(key, body.template_id, body.seed_token, body.kind, body.seats)
        if body.kind == "house" and body.first_move and snap.state_version == 0:
            await service.submit_move(snap.id, key, f"first-{snap.id}", 0, body.first_move)
            snap = await service.snapshot(snap.id, key)
        return snap

    @app.post("/tables/join")
    async def join_table(body: JoinTable, request: Request, response: Response) -> Seated:
        key = await player_session(request, response, body.stage_name)
        return Seated(match_id=await service.join(body.invite_code, key))

    @app.post("/tables/quick")
    async def quick_match(body: QuickMatch, request: Request, response: Response) -> Seated:
        key = await player_session(request, response, body.stage_name)
        return Seated(match_id=await service.quick_match(key, body.template_id, body.seats))

    @app.post("/matches/{match_id}/seats/house", status_code=204)
    async def add_house(match_id: str, request: Request) -> Response:
        await service.add_house(match_id, session_of(request))
        return Response(status_code=204)

    @app.get("/tables")
    async def get_tables() -> list[TableView]:
        return await service.open_tables()

    @app.websocket("/ws")
    async def socket(websocket: WebSocket) -> None:
        key = websocket.cookies.get(SESSION_COOKIE)
        await websocket.accept()
        if not key:
            await websocket.close(code=4401)
            return
        presence.add(key, websocket)
        try:
            await presence.broadcast(Online(count=presence.count))
            await websocket.send_text(Lobby(tables=await service.open_tables()).model_dump_json())
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            presence.remove(key, websocket)
            await presence.broadcast(Online(count=presence.count))

    @app.get("/ws-payloads", include_in_schema=True)
    async def ws_payloads() -> WsPayloads:
        raise HTTPException(404, "schema-only endpoint")

    @app.get("/profiles/{account_id}")
    async def get_profile(account_id: str, request: Request) -> ProfileView:
        return await profile(service, account_id, request.cookies.get(SESSION_COOKIE))

    @app.get("/matches/{match_id}")
    async def get_match(match_id: str, request: Request) -> MatchSnapshot:
        return await service.snapshot(match_id, request.cookies.get(SESSION_COOKIE))

    @app.post("/matches/{match_id}/visibility", status_code=204)
    async def post_visibility(match_id: str, body: VisibilityCommand, request: Request) -> Response:
        await service.set_visibility(match_id, session_of(request), body.public)
        return Response(status_code=204)

    @app.post("/matches/{match_id}/moves", status_code=202)
    async def post_move(match_id: str, body: MoveCommand, request: Request) -> Accepted:
        await service.submit_move(
            match_id,
            session_of(request),
            body.action_id,
            body.expected_version,
            body.move_text,
            body.round_n,
        )
        return Accepted()

    @app.post("/matches/{match_id}/guesses", status_code=202)
    async def post_guess(match_id: str, body: GuessCommand, request: Request) -> Accepted:
        await service.submit_guess(
            match_id,
            session_of(request),
            body.action_id,
            body.expected_version,
            body.key,
            body.round_n,
        )
        return Accepted()

    @app.post("/matches/{match_id}/resign", status_code=202)
    async def post_resign(match_id: str, body: ResignCommand, request: Request) -> Accepted:
        await service.resign_match(
            match_id, session_of(request), body.action_id, body.expected_version
        )
        return Accepted()

    @app.post("/matches/{match_id}/turns/{seq}/disagree", status_code=204)
    async def post_disagree(match_id: str, seq: int, request: Request) -> Response:
        await service.disagree(match_id, seq, request.cookies.get(SESSION_COOKIE))
        return Response(status_code=204)

    @app.get("/matches/{match_id}/events")
    async def events(
        match_id: str, last_event_id: str | None = Header(default=None)
    ) -> EventSourceResponse:
        await service.snapshot(match_id)
        last_id = bus.cursor_from(last_event_id)

        async def gen() -> AsyncIterator[dict]:
            async for event in bus.subscribe(match_id, last_id):
                yield {"id": event.id, "event": event.name, "data": event.data}

        return EventSourceResponse(gen())

    @app.get("/sse-payloads", include_in_schema=True)
    async def sse_payloads() -> SsePayloads:
        raise HTTPException(404, "schema-only endpoint")

    @app.get("/on-stage")
    async def get_stage() -> StageView:
        return await service.stage()

    @app.get("/replays/{match_id}")
    async def get_replay(match_id: str, request: Request) -> Replay:
        return await service.replay(match_id, request.cookies.get(SESSION_COOKIE))

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

    shell_page = (
        (settings.frontend_dist / "index.html").read_text() if settings.frontend_dist else None
    )

    @app.get("/r/{match_id}", response_class=HTMLResponse)
    async def replay_shell(match_id: str) -> HTMLResponse:
        replay = await service.replay(match_id)
        players = " vs ".join(s.model or s.display_name for s in replay.seats)
        title = html.escape(f"{players}, {replay.title}")
        description = html.escape(replay.share_text)
        head = (
            f'<meta property="og:title" content="{title}">'
            f'<meta property="og:description" content="{description}">'
            f'<meta property="og:url" content="{settings.public_base_url}/r/{match_id}">'
        )
        if shell_page is not None:
            return HTMLResponse(shell_page.replace("</head>", head + "</head>", 1))
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

    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(root / "index.html")


app = build_app()
