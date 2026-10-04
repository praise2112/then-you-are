import dataclasses
import os
import secrets

import httpx
import pytest
from asgi_lifespan import LifespanManager

from arena_core.template import load_templates
from arena_server import auth
from arena_server.app import build_app
from arena_server.config import load_settings
from arena_server.leaderboard import streaks
from tests.conftest import FakeCaller, judge_response
from tests.test_api import settle

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL pointing at Postgres",
    ),
    pytest.mark.xdist_group("database"),
]

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
    assert streaks([True, True, None, False, True]) == (1, 2)
    assert streaks([False, True, True, True]) == (3, 3)


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

        signed_in_key = client.cookies.get(auth.SESSION_COOKIE)
        assert signed_in_key
        out = await client.post("/auth/logout")
        assert out.status_code == 204
        assert (await client.get("/sessions/me")).json()["account"] is None
        replay = httpx.AsyncClient(transport=client._transport, base_url="http://test")
        replay.cookies.set(auth.SESSION_COOKIE, signed_in_key)
        assert (await replay.get("/sessions/me")).json()["account"] is None
        await replay.aclose()

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
        assert 1 <= len(board["standings"]) <= 20
        assert board["standings"][0]["rank"] == 1
        account_id = me["account"]["id"]
        record = (await client.get(f"/profiles/{account_id}")).json()["records"]
        assert [(r["slug"], r["played"], r["won"]) for r in record] == [("then-i-am", 3, 1)]
        assert record[0]["rank"] >= 1
        other = (await client.get("/leaderboard/word-for-word")).json()
        assert all(s["account_id"] != account_id for s in other["standings"])
        assert (await client.get("/leaderboard/no-such-game")).status_code == 404
        index = (await client.get("/leaderboard")).json()
        assert [b["slug"] for b in index][0] == "then-i-am" and len(index) == len(load_templates())
        assert index[0]["emblem"] and index[0]["accent"].startswith("#")
        assert index[0]["ranked"] >= 1 and index[0]["leader"] is not None
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


@pytest.mark.anyio
async def test_a_game_against_people_is_listed_but_never_counted(monkeypatch):
    from tests.test_tables import expire, join, player, table

    host = auth.Profile("github", f"{RUN}-t", f"Tia {RUN}", "")

    async def fake_profile(_client, _provider, _request):
        return host

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    app, manager, client = await run_app(FakeCaller([], []))
    guest = player(app)
    try:
        await sign_in(client)
        opened = await table(client, "then-i-am", 2, "Tia")
        await join(guest, opened["invite_code"], "Ben")
        await settle(app)
        await expire(app, opened["id"])
        await guest.post(
            f"/matches/{opened['id']}/moves",
            json={"action_id": "b1", "expected_version": 1, "move_text": "I am rain."},
        )
        await settle(app)
        await expire(app, opened["id"])

        me = (await client.get("/sessions/me")).json()
        assert (me["account"]["streak"], me["account"]["best_streak"]) == (0, 0)
        shown = (await client.get(f"/profiles/{me['account']['id']}")).json()
        assert shown["records"] == [] and shown["duels"] == [] and shown["played"] == 0
        assert [(d["result"], d["against"]) for d in shown["people"]] == [("Out of turns", "Ben")]
        board = (await client.get("/leaderboard/then-i-am")).json()
        assert all(s["account_id"] != me["account"]["id"] for s in board["standings"])
    finally:
        await guest.aclose()
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_deleting_an_account_drops_its_house_games_and_unnames_its_seat_at_shared_ones(
    monkeypatch,
):
    from tests.test_tables import expire, join, player, table

    leaver = auth.Profile("github", f"{RUN}-x", f"Xan {RUN}", "")

    async def fake_profile(_client, _provider, _request):
        return leaver

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    app, manager, client = await run_app(FakeCaller([], []))
    guest = player(app)
    try:
        await sign_in(client)
        account_id = (await client.get("/sessions/me")).json()["account"]["id"]
        house = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        await client.post(
            f"/matches/{house['id']}/resign", json={"action_id": "r", "expected_version": 0}
        )
        await settle(app)
        shared = await table(client, "then-i-am", 2, "Xan")
        await join(guest, shared["invite_code"], "Ben")
        await settle(app)
        await expire(app, shared["id"])
        await guest.post(
            f"/matches/{shared['id']}/moves",
            json={"action_id": "b1", "expected_version": 1, "move_text": "I am rain."},
        )
        await settle(app)
        await expire(app, shared["id"])
        before = (await guest.get(f"/replays/{shared['id']}")).json()["transcript"]
        assert "I am rain." in [t["move_text"] for t in before]

        assert (await client.delete("/sessions/me/account")).status_code == 204

        assert (await client.get("/sessions/me")).json()["account"] is None
        assert (await client.get(f"/replays/{house['id']}")).status_code == 404
        assert (await client.get(f"/profiles/{account_id}")).status_code == 404
        replay = (await guest.get(f"/replays/{shared['id']}")).json()
        assert [s["display_name"] for s in replay["seats"]] == ["Deleted player", "Ben"]
        assert replay["transcript"] == before

        await sign_in(client)
        assert (await client.get("/sessions/me")).json()["account"]["id"] != account_id
    finally:
        await guest.aclose()
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_an_unfinished_house_game_blocks_deletion_and_a_guest_has_nothing_to_delete(
    monkeypatch,
):
    stayer = auth.Profile("github", f"{RUN}-y", f"Yan {RUN}", "")

    async def fake_profile(_client, _provider, _request):
        return stayer

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    app, manager, client = await run_app(FakeCaller([], []))
    try:
        await client.put("/sessions/me", json={"stage_name": "Echo"})
        assert (await client.delete("/sessions/me/account")).status_code == 404

        await sign_in(client)
        await client.post("/matches", json={"template_id": "then-i-am"})
        refused = await client.delete("/sessions/me/account")
        assert refused.status_code == 409
        assert (await client.get("/sessions/me")).json()["account"] is not None
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_guest_unseen_for_a_year_is_forgotten_unless_a_game_is_unfinished():
    from tests.test_tables import expire, join, player, table

    app, manager, gone = await run_app(FakeCaller([], []))
    kept, busy = player(app), player(app)
    pool = app.state.service.pool

    async def age(client: httpx.AsyncClient, days: int) -> None:
        async with pool.connection() as conn:
            await conn.execute(
                "update sessions set last_seen_at = now() - make_interval(days => %s) "
                "where session_key = %s",
                (days, client.cookies.get(auth.SESSION_COOKIE)),
            )

    async def last_seen(client: httpx.AsyncClient):
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    "select last_seen_at > now() - interval '1 minute' as recent from sessions "
                    "where session_key = %s",
                    (client.cookies.get(auth.SESSION_COOKIE),),
                )
            ).fetchone()
        return row and row["recent"]

    try:
        await gone.put("/sessions/me", json={"stage_name": "Gus"})
        solo = (await gone.post("/matches", json={"template_id": "then-i-am"})).json()
        await gone.post(
            f"/matches/{solo['id']}/resign", json={"action_id": "r", "expected_version": 0}
        )
        await settle(app)
        shared = await table(gone, "then-i-am", 2, "Gus")
        await join(kept, shared["invite_code"], "Ben")
        await settle(app)
        await expire(app, shared["id"])
        await kept.post(
            f"/matches/{shared['id']}/moves",
            json={"action_id": "b1", "expected_version": 1, "move_text": "I am rain."},
        )
        await settle(app)
        await expire(app, shared["id"])
        await busy.post("/matches", json={"template_id": "then-i-am", "stage_name": "Ivy"})

        await age(kept, 2)
        await kept.get("/sessions/me")
        assert await last_seen(kept)
        await age(gone, 400)
        await age(busy, 400)

        assert await auth.forget_stale_guests(pool) >= 1

        replay = (await kept.get(f"/replays/{shared['id']}")).json()
        assert [s["display_name"] for s in replay["seats"]] == ["Deleted player", "Ben"]
        assert (await kept.get(f"/replays/{solo['id']}")).status_code == 404
        assert (await busy.get("/sessions/me")).json()["stage_name"] == "Ivy"
    finally:
        await kept.aclose()
        await busy.aclose()
        await gone.aclose()
        await manager.__aexit__(None, None, None)


def test_a_sign_in_only_returns_to_a_page_on_this_site():
    assert auth.local_path("/play/then-i-am?x=1") == "/play/then-i-am?x=1"
    assert auth.local_path("//evil.example/phish") == "/"
    assert auth.local_path("/\\evil.example") == "/"
    assert auth.local_path("https://evil.example") == "/"
