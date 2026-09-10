import dataclasses
import os
import secrets

import httpx
import pytest
from asgi_lifespan import LifespanManager

from arena_server import auth
from arena_server.app import build_app
from arena_server.config import load_settings
from tests.conftest import FakeCaller, judge_response
from tests.test_api import settle

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL pointing at Postgres"
)

# The test database keeps rows between runs, so each run signs in as a new person.
RUN = secrets.token_hex(4)
PROFILE = auth.Profile("github", RUN, f"Ada {RUN}", "https://avatars.example/ada.png")
PLAYER = auth.Profile("github", f"{RUN}-b", f"Bea {RUN}", "")


async def run_app(caller: FakeCaller):
    settings = dataclasses.replace(
        load_settings(),
        database_url=os.environ["TEST_DATABASE_URL"],
        oauth_clients={"github": ("id", "secret")},
    )
    app = build_app(settings, caller)
    manager = LifespanManager(app)
    await manager.__aenter__()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return app, manager, client


async def sign_in(client: httpx.AsyncClient) -> None:
    landed = await client.get("/auth/github/callback?code=c&state=s")
    assert landed.status_code == 303
    assert landed.headers["location"] == "/"


async def resign(client: httpx.AsyncClient, app) -> None:
    match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
    await client.post(
        f"/matches/{match['id']}/resign", json={"action_id": "r", "expected_version": 0}
    )
    await settle(app)


def test_public_names_are_first_names_only():
    assert auth.first_name("Ada King Lovelace", "ada") == "Ada"
    assert auth.first_name("   ", "ada") == "ada"
    assert auth.first_name(None, "ada") == "ada"


@pytest.mark.anyio
async def test_login_redirects_to_the_provider_and_unknown_providers_are_404(monkeypatch):
    app, manager, client = await run_app(FakeCaller([], []))
    try:
        hop = await client.get("/auth/github/login?next=/leaderboard")
        assert hop.status_code == 302
        assert hop.headers["location"].startswith("https://github.com/login/oauth/authorize?")
        assert "state=" in hop.headers["location"]
        assert (await client.get("/auth/google/login")).status_code == 404
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_signing_in_claims_the_guest_session_and_signing_out_leaves_it(monkeypatch):
    async def fake_profile(_client, _provider, _request):
        return PROFILE

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    app, manager, client = await run_app(FakeCaller([], []))
    try:
        guest = await client.put("/sessions/me", json={"stage_name": "Echo"})
        assert guest.json()["account"] is None
        assert guest.json()["providers"] == ["github"]

        await sign_in(client)
        me = (await client.get("/sessions/me")).json()
        assert me["account"] == {
            "provider": "github",
            "display_name": PROFILE.display_name,
            "avatar_url": "https://avatars.example/ada.png",
        }
        assert me["stage_name"] == PROFILE.display_name

        chosen = f"Countess {RUN}"
        renamed = (await client.put("/sessions/me", json={"stage_name": chosen})).json()
        assert renamed["stage_name"] == chosen
        assert renamed["account"]["display_name"] == chosen

        out = await client.post("/auth/logout")
        assert out.status_code == 204
        assert (await client.get("/sessions/me")).json()["account"] is None

        await sign_in(client)
        me = (await client.get("/sessions/me")).json()
        assert me["account"]["display_name"] == chosen
        assert me["stage_name"] == chosen
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_leaderboard_counts_an_account_across_its_sessions(monkeypatch):
    async def fake_profile(_client, _provider, _request):
        return PLAYER

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    caller = FakeCaller(
        rulings=[judge_response(), judge_response(verdict="fail")],
        opponent_moves=["I am a hammer, rock-splitting."],
    )
    app, manager, client = await run_app(caller)
    try:
        await sign_in(client)
        await resign(client, app)
        await resign(client, app)
        await client.post("/auth/logout")
        await sign_in(client)
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "a1", "expected_version": 0, "move_text": "I am rain."},
        )
        await settle(app)

        boards = (await client.get("/leaderboard")).json()
        assert [b["slug"] for b in boards] == ["then-i-am", "word-for-word"]
        mine = [s for s in boards[0]["standings"] if s["display_name"] == PLAYER.display_name]
        assert len(mine) == 1
        assert mine[0]["wins"] == 1
        assert mine[0]["played"] == 3
        assert all(s["display_name"] != PLAYER.display_name for s in boards[1]["standings"])
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)
