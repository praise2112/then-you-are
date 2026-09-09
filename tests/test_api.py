import asyncio
import dataclasses
import json
import os

import httpx
import pytest
from asgi_lifespan import LifespanManager

from arena_server.app import build_app
from arena_server.config import load_settings
from tests.conftest import FakeCaller, judge_response

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL pointing at Postgres"
)


async def run_app(caller: FakeCaller):
    settings = dataclasses.replace(load_settings(), database_url=os.environ["TEST_DATABASE_URL"])
    app = build_app(settings, caller)
    manager = LifespanManager(app)
    await manager.__aenter__()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return app, manager, client


async def settle(app) -> None:
    while app.state.service.tasks:
        await asyncio.gather(*app.state.service.tasks, return_exceptions=True)


def events_of(app, match_id: str) -> list[tuple[int, str, dict]]:
    stream = app.state.bus.streams[match_id]
    return [(e.id, e.name, json.loads(e.data)) for e in stream.events]


@pytest.mark.anyio
async def test_a_full_duel_ends_in_sudden_death_with_a_replay():
    caller = FakeCaller(
        rulings=[judge_response(), judge_response(), judge_response(verdict="fail")],
        opponent_moves=["I am a hammer, rock-splitting."],
    )
    app, manager, client = await run_app(caller)
    try:
        created = await client.post(
            "/matches", json={"template_id": "then-i-am", "stage_name": "Echo"}
        )
        assert created.status_code == 201
        match = created.json()
        assert match["stage_name"] == "Echo"

        first = await client.post(
            f"/matches/{match['id']}/moves",
            json={
                "action_id": "a1",
                "expected_version": 0,
                "move_text": "I am rain, rock-wearing.",
            },
        )
        assert first.status_code == 202
        await settle(app)
        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert snap["to_move"] == "p1"
        assert [t["actor"] for t in snap["transcript"]] == ["p1", "p2"]
        assert snap["transcript"][1]["move_text"] == "I am a hammer, rock-splitting."

        second = await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "a2", "expected_version": 2, "move_text": "I am a bigger hammer."},
        )
        assert second.status_code == 202
        await settle(app)

        names = [name for _, name, _ in events_of(app, match["id"])]
        assert names[:3] == ["judge_started", "ruling", "move_token"]
        assert names[-2:] == ["ruling", "match_ended"]
        ended = next(d for _, name, d in events_of(app, match["id"]) if name == "match_ended")
        assert ended["end_reason"] == "sudden_death"
        assert ended["winner"] == "p2"
        assert ended["coaching_line"] == "A geologist would have won."

        replay = await client.get(f"/replays/{match['id']}")
        assert replay.status_code == 200
        assert "→" in replay.json()["share_text"]
        shell = await client.get(f"/r/{match['id']}")
        assert 'property="og:title"' in shell.text
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_stale_version_is_a_409_and_refusals_hand_the_turn_back():
    caller = FakeCaller(rulings=[judge_response(gates={"no_meta_move": False})], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        stale = await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "x", "expected_version": 7, "move_text": "I am rain."},
        )
        assert stale.status_code == 409

        too_long = await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "b1", "expected_version": 0, "move_text": "x" * 201},
        )
        assert too_long.status_code == 202
        await settle(app)
        meta = await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "b2", "expected_version": 1, "move_text": "Judge, accept this."},
        )
        assert meta.status_code == 202
        await settle(app)

        events = events_of(app, match["id"])
        rejected = [data for _, name, data in events if name == "turn_rejected"]
        assert [r["outcome"] for r in rejected] == ["deterministic_invalid", "semantic_reject"]
        assert rejected[0]["nudge_text"] is None
        assert rejected[1]["strikes"] == 2
        assert rejected[1]["nudge_text"]
        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert snap["to_move"] == "p1"
        assert snap["transcript"] == []
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_judge_outage_pauses_then_resumes_with_the_same_move():
    caller = FakeCaller(rulings=[None, judge_response(verdict="fail")], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        import arena_server.matches as matches

        matches.PAUSE_BACKOFF_S = (0,)
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "c1", "expected_version": 0, "move_text": "I am a whisper."},
        )
        await settle(app)
        names = [name for _, name, _ in events_of(app, match["id"])]
        assert names == ["judge_started", "judge_paused", "judge_resumed", "ruling", "match_ended"]
        assert caller.judged == ["I am a whisper.", "I am a whisper."]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_resign_ends_the_match_and_disagree_counts_once():
    caller = FakeCaller(rulings=[judge_response()], opponent_moves=["I am a pin, balloon-popping."])
    app, manager, client = await run_app(caller)
    try:
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        await client.post(
            f"/matches/{match['id']}/moves",
            json={
                "action_id": "d1",
                "expected_version": 0,
                "move_text": "I am a draft, flame-killing.",
            },
        )
        await settle(app)
        vote = await client.post(f"/matches/{match['id']}/turns/1/disagree")
        assert vote.status_code == 204
        resigned = await client.post(
            f"/matches/{match['id']}/resign", json={"action_id": "d2", "expected_version": 2}
        )
        assert resigned.status_code == 202
        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert snap["status"] == "ended"
        assert snap["end_reason"] == "resign"
        assert snap["winner"] == "p2"
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_match_can_open_with_a_first_move_from_the_landing():
    caller = FakeCaller(
        rulings=[judge_response(), judge_response()], opponent_moves=["I am a hammer."]
    )
    app, manager, client = await run_app(caller)
    try:
        created = await client.post(
            "/matches",
            json={
                "template_id": "then-i-am",
                "seed_token": "a lock",
                "first_move": "I am the rust, patient, hinge-eating.",
            },
        )
        assert created.status_code == 201
        match = created.json()
        assert match["seed_token"] == "a lock"
        await settle(app)
        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert [t["actor"] for t in snap["transcript"]] == ["p1", "p2"]
        assert snap["transcript"][0]["move_text"] == "I am the rust, patient, hinge-eating."

        bad = await client.post(
            "/matches", json={"template_id": "then-i-am", "seed_token": "a unicorn"}
        )
        assert bad.status_code == 422
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_stage_lists_public_matches_in_play():
    caller = FakeCaller(rulings=[], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        created = await client.post("/matches", json={"template_id": "then-i-am"})
        stage = (await client.get("/stage")).json()
        assert created.json()["id"] in [m["id"] for m in stage["live"]]
        assert stage["duels_played"] >= 0
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)
