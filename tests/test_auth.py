import dataclasses
import os
import secrets

import httpx
import pytest
from asgi_lifespan import LifespanManager

from arena_server import auth
from arena_server.app import build_app
from arena_server.config import load_settings
from arena_server.leaderboard import streaks
from tests.conftest import FakeCaller, judge_response
from tests.test_api import settle

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL pointing at Postgres"
)

# The test database keeps rows between runs, so each run signs in as a new person.
RUN = secrets.token_hex(4)
PROFILE = auth.Profile("github", RUN, f"Ada {RUN}", "https://avatars.example/ada.png")
PLAYER = auth.Profile("github", f"{RUN}-b", f"Bea {RUN}", "")
LINKER = auth.Profile("github", f"{RUN}-l", f"Lin {RUN}", "")
SECOND = auth.Profile("discord", f"{RUN}-d", f"lin_{RUN}", "https://cdn.example/d.png")


async def run_app(caller: FakeCaller):
    settings = dataclasses.replace(
        load_settings(),
        database_url=os.environ["TEST_DATABASE_URL"],
        oauth_clients={"github": ("id", "secret"), "discord": ("id", "secret")},
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


def test_streaks_skip_draws_and_reset_on_a_loss():
    assert streaks([]) == (0, 0)
    assert streaks(["p1", "p1", None, "p2", "p1"]) == (1, 2)
    assert streaks(["p2", "p1", "p1", "p1"]) == (3, 3)


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
        assert guest.json()["providers"] == ["github", "discord"]

        await sign_in(client)
        me = (await client.get("/sessions/me")).json()
        assert me["account"].pop("id")
        assert me["account"] == {
            "providers": ["github"],
            "display_name": PROFILE.display_name,
            "avatar_url": "https://avatars.example/ada.png",
            "streak": 0,
            "best_streak": 0,
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

        me = (await client.get("/sessions/me")).json()
        assert (me["account"]["streak"], me["account"]["best_streak"]) == (1, 1)

        board = (await client.get("/leaderboard/then-i-am")).json()
        assert board["title"] == "Then I Am"
        mine = [s for s in board["standings"] if s["display_name"] == PLAYER.display_name]
        assert len(mine) == 1
        assert mine[0]["wins"] == 1
        assert mine[0]["played"] == 3
        other = (await client.get("/leaderboard/word-for-word")).json()
        assert all(s["display_name"] != PLAYER.display_name for s in other["standings"])
        assert (await client.get("/leaderboard/no-such-game")).status_code == 404
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_signed_in_player_links_a_second_provider_to_the_same_account(monkeypatch):
    profiles = [LINKER, SECOND, SECOND, SECOND]

    async def fake_profile(_client, _provider, _request):
        return profiles.pop(0)

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    app, manager, client = await run_app(FakeCaller([], []))
    try:
        await client.get("/auth/github/callback?code=c&state=s")
        linked = await client.get("/auth/discord/callback?code=c&state=s")
        assert linked.headers["location"] == f"/?account=linked:discord:{SECOND.display_name}"
        me = (await client.get("/sessions/me")).json()
        assert me["account"]["providers"] == ["github", "discord"]
        assert me["account"]["display_name"] == LINKER.display_name

        again = await client.get("/auth/discord/callback?code=c&state=s")
        assert again.headers["location"].startswith("/?account=linked:discord")

        await client.post("/auth/logout")
        await client.get("/auth/discord/callback?code=c&state=s")
        me = (await client.get("/sessions/me")).json()
        assert me["account"]["providers"] == ["github", "discord"]
        assert me["stage_name"] == LINKER.display_name
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_an_identity_on_another_account_is_not_moved(monkeypatch):
    other = auth.Profile("discord", f"{RUN}-x", f"Xan {RUN}", "")
    profiles = [other, PLAYER, other]

    async def fake_profile(_client, _provider, _request):
        return profiles.pop(0)

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    app, manager, client = await run_app(FakeCaller([], []))
    try:
        await client.get("/auth/discord/callback?code=c&state=s")
        await client.post("/auth/logout")
        await sign_in(client)
        taken = await client.get("/auth/discord/callback?code=c&state=s")
        assert taken.headers["location"] == f"/?account=taken:discord:Xan%20{RUN}"
        me = (await client.get("/sessions/me")).json()
        assert me["account"]["display_name"] == PLAYER.display_name
        assert me["account"]["providers"] == ["github"]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_profile_shows_the_record_and_hides_private_duels_from_visitors(monkeypatch):
    who = auth.Profile("github", f"{RUN}-p", f"Pia {RUN}", "")

    async def fake_profile(_client, _provider, _request):
        return who

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    caller = FakeCaller(
        rulings=[judge_response(), judge_response(verdict="fail")],
        opponent_moves=["I am a hammer, rock-splitting."],
    )
    app, manager, client = await run_app(caller)
    try:
        await sign_in(client)
        await resign(client, app)
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "a1", "expected_version": 0, "move_text": "I am rain."},
        )
        await settle(app)
        await client.post("/matches", json={"template_id": "word-for-word"})

        account_id = (await client.get("/sessions/me")).json()["account"]["id"]
        mine = (await client.get(f"/profiles/{account_id}")).json()
        assert mine["is_yours"] is True
        assert (mine["played"], mine["won"], mine["streak"]) == (2, 1, 1)
        assert mine["records"] == [
            {
                "slug": "then-i-am",
                "title": "Then I Am",
                "played": 2,
                "won": 1,
                "drawn": 0,
                "best_streak": 1,
                "rank": None,
            }
        ]
        assert [d["result"] for d in mine["duels"]] == ["On stage", "Victory", "Resigned"]
        assert [r["id"] for r in mine["best"]] == [match["id"]]

        stranger = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        theirs = (await stranger.get(f"/profiles/{account_id}")).json()
        await stranger.aclose()
        assert theirs["is_yours"] is False
        assert theirs["duels"] == []
        assert theirs["played"] == 2
        assert (await client.get("/profiles/nobody")).status_code == 404
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)
