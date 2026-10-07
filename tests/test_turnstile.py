import os

import httpx
import pytest

from arena_server import auth
from arena_server.tables import NOT_SEEN
from arena_server.turnstile import passes_turnstile
from tests.conftest import FakeCaller, run_app, settle
from tests.test_auth import RUN, sign_in

needs_database = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL pointing at Postgres"
)


def siteverify(monkeypatch, handler) -> list[httpx.Request]:
    """Routes Cloudflare's siteverify to `handler`; returns the requests it was sent."""
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(record), **kw)
    )
    return sent


def human_once():
    """Passes the first check and fails every later one."""
    answers = iter([True])

    async def verify(_token: str) -> bool:
        return next(answers, False)

    return verify


async def never_human(_token: str) -> bool:
    return False


@pytest.mark.anyio
async def test_a_token_passes_only_when_cloudflare_says_so(monkeypatch):
    replies = iter([{"success": True}, {"success": False, "error-codes": ["invalid-input"]}])
    sent = siteverify(monkeypatch, lambda _r: httpx.Response(200, json=next(replies)))
    assert await passes_turnstile("sec", "tok")
    assert not await passes_turnstile("sec", "tok")
    assert dict(httpx.QueryParams(sent[0].content.decode())) == {"secret": "sec", "response": "tok"}


@pytest.mark.anyio
async def test_an_empty_token_or_an_unreachable_cloudflare_fails_the_check(monkeypatch):
    def unreachable(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    sent = siteverify(monkeypatch, unreachable)
    assert not await passes_turnstile("sec", "")
    assert sent == []
    assert not await passes_turnstile("sec", "tok")


@needs_database
@pytest.mark.xdist_group("database")
@pytest.mark.anyio
async def test_a_guest_failing_the_check_cannot_start_a_duel():
    app, manager, client = await run_app(FakeCaller([], []), never_human)
    try:
        refused = await client.post("/matches", json={"template_id": "then-i-am"})
        assert (refused.status_code, refused.json()["detail"]) == (403, NOT_SEEN)
        assert (await client.get("/sessions/me")).json()["open_duels"] == []
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@needs_database
@pytest.mark.xdist_group("database")
@pytest.mark.anyio
async def test_a_guest_resuming_an_open_duel_is_not_checked_again():
    app, manager, client = await run_app(FakeCaller([], []), human_once())
    try:
        started = await client.post("/matches", json={"template_id": "then-i-am"})
        await settle(app)
        resumed = await client.post("/matches", json={"template_id": "then-i-am"})
        assert resumed.status_code == 201 and resumed.json()["id"] == started.json()["id"]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@needs_database
@pytest.mark.xdist_group("database")
@pytest.mark.anyio
async def test_a_signed_in_player_is_not_checked(monkeypatch):
    async def fake_profile(_client, _provider, _request):
        return auth.Profile("github", f"{RUN}-ts", f"Tess {RUN}", "")

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    app, manager, client = await run_app(FakeCaller([], []), never_human)
    try:
        await sign_in(client)
        assert (await client.post("/matches", json={"template_id": "then-i-am"})).status_code == 201
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)
