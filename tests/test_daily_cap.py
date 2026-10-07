import os

import pytest

from arena_server import auth
from arena_server.store import add_spend, spent_today
from arena_server.tables import CLOSED_FOR_TODAY
from tests.conftest import FakeCaller, run_app, settle
from tests.test_auth import RUN, resign, sign_in

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL pointing at Postgres",
    ),
    pytest.mark.xdist_group("database"),
]


async def capped_app(headroom: float):
    """The app with today's cap set `headroom` dollars above what is already spent today."""
    app, manager, client = await run_app(FakeCaller([], []))
    cap = await spent_today(app.state.service.pool) + headroom
    await manager.__aexit__(None, None, None)
    await client.aclose()
    return await run_app(FakeCaller([], []), daily_cap_usd=cap)


async def spend_past_cap(app) -> None:
    await add_spend(app.state.service.pool, 1.0)


async def match_count(app) -> int:
    async with app.state.service.pool.connection() as conn:
        row = await (await conn.execute("select count(*) as n from matches")).fetchone()
    assert row is not None
    return row["n"]


@pytest.mark.anyio
async def test_a_new_duel_past_the_cap_is_refused_and_creates_no_match():
    app, manager, client = await capped_app(0.5)
    try:
        await spend_past_cap(app)
        before = await match_count(app)
        refused = await client.post("/matches", json={"template_id": "then-i-am"})
        assert (refused.status_code, refused.json()["detail"]) == (503, CLOSED_FOR_TODAY)
        assert await match_count(app) == before
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_duel_started_before_the_cap_still_resumes_past_it():
    app, manager, client = await capped_app(0.5)
    try:
        started = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        await settle(app)
        await spend_past_cap(app)
        resumed = await client.post("/matches", json={"template_id": "then-i-am"})
        assert resumed.status_code == 201 and resumed.json()["id"] == started["id"]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_quick_match_past_the_cap_is_refused():
    app, manager, client = await capped_app(0.5)
    try:
        await spend_past_cap(app)
        refused = await client.post("/tables/quick", json={"template_id": "then-i-am"})
        assert (refused.status_code, refused.json()["detail"]) == (503, CLOSED_FOR_TODAY)
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_house_cannot_fill_a_waiting_table_past_the_cap():
    app, manager, client = await capped_app(0.5)
    try:
        table = await client.post(
            "/matches", json={"template_id": "then-i-am", "kind": "friends", "seats": 2}
        )
        await spend_past_cap(app)
        refused = await client.post(f"/matches/{table.json()['id']}/seats/house")
        assert (refused.status_code, refused.json()["detail"]) == (503, CLOSED_FOR_TODAY)
        assert (await client.get(f"/matches/{table.json()['id']}")).json()["status"] == "open"
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_deleting_an_account_leaves_todays_spend_unchanged(monkeypatch):
    async def fake_profile(_client, _provider, _request):
        return auth.Profile("github", f"{RUN}-cap", f"Cap {RUN}", "")

    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    app, manager, client = await run_app(FakeCaller([], []))
    pool = app.state.service.pool
    try:
        await sign_in(client)
        await resign(client, app)
        await add_spend(pool, 0.25)
        before = await spent_today(pool)
        assert (await client.delete("/sessions/me/account")).status_code == 204
        assert await spent_today(pool) == before
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)
