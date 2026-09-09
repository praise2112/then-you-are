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
    settings = dataclasses.replace(
        load_settings(), database_url=os.environ["TEST_DATABASE_URL"], curator_token="shh"
    )
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
async def test_duels_are_private_unless_the_player_lists_them():
    caller = FakeCaller(rulings=[], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        quiet = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        assert quiet["is_public"] is False and quiet["is_yours"] is True
        assert quiet["id"] not in [m["id"] for m in (await client.get("/on-stage")).json()["live"]]

        me = (await client.put("/sessions/me", json={"list_duels": True})).json()
        assert me["list_duels"] is True
        listed = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        assert listed["is_public"] is True
        assert listed["id"] in [m["id"] for m in (await client.get("/on-stage")).json()["live"]]

        shown = await client.post(f"/matches/{quiet['id']}/visibility", json={"public": True})
        assert shown.status_code == 204
        assert (await client.get(f"/matches/{quiet['id']}")).json()["is_public"] is True

        stranger = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        denied = await stranger.post(f"/matches/{quiet['id']}/visibility", json={"public": False})
        assert denied.status_code == 401
        await stranger.aclose()
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_replay_lists_sort_and_curation_needs_the_token():
    caller = FakeCaller(rulings=[judge_response(verdict="fail")], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        await client.put("/sessions/me", json={"list_duels": True})
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "a1", "expected_version": 0, "move_text": "I am a bigger rock."},
        )
        await settle(app)
        newest = (await client.get("/replays?sort=newest")).json()
        assert newest[0]["id"] == match["id"]
        assert newest[0]["is_curated"] is False
        assert match["id"] not in [r["id"] for r in (await client.get("/replays")).json()]

        denied = await client.post(f"/replays/{match['id']}/curate", json={"curated": True})
        assert denied.status_code == 403
        ok = await client.post(
            f"/replays/{match['id']}/curate",
            json={"curated": True},
            headers={"x-curator-token": "shh"},
        )
        assert ok.status_code == 204
        curated = (await client.get("/replays?sort=curated")).json()
        assert curated[0]["id"] == match["id"] and curated[0]["is_curated"] is True
        lengths = [len(r["transcript"]) for r in (await client.get("/replays?sort=longest")).json()]
        assert lengths == sorted(lengths, reverse=True)
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_word_duel_holds_the_house_bluff_until_the_reveal():
    caller = FakeCaller(
        rulings=[
            judge_response(truth_proximity="hit"),
            judge_response(),
            judge_response(verdict="fail"),
            judge_response(),
            judge_response(confidence="coin_flip"),
            judge_response(truth_proximity="near"),
        ],
        opponent_moves=["a cup holder", "a low groan", "a hinge pin"],
    )
    app, manager, client = await run_app(caller)
    try:
        created = await client.post(
            "/matches", json={"template_id": "word-for-word", "seed_token": "zarf"}
        )
        assert created.status_code == 201
        match = created.json()
        assert match["mode"] == "showcase" and match["title"] == "Word for Word"
        assert [r["token"] for r in match["rounds"]] == ["zarf"]
        assert match["rounds"][0]["truth"] is None and match["rounds"][0]["detail"]
        await settle(app)
        assert (await client.get(f"/matches/{match['id']}")).json()["transcript"] == []

        for n, (version, bluff) in enumerate(
            [(0, "a desert cloak"), (2, "to shell peas"), (4, "a sailor's knot")], start=1
        ):
            posted = await client.post(
                f"/matches/{match['id']}/moves",
                json={"action_id": f"r{n}", "expected_version": version, "move_text": bluff},
            )
            assert posted.status_code == 202
            await settle(app)

        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert snap["status"] == "ended" and snap["end_reason"] == "rounds_complete"
        assert [t["round_n"] for t in snap["transcript"]] == [1, 1, 2, 2, 3, 3]
        assert [t["actor"] for t in snap["transcript"]] == ["p1", "p2"] * 3
        assert snap["transcript"][1]["move_text"] == "a cup holder"
        assert all(r["truth"] for r in snap["rounds"]) and len(snap["rounds"]) == 3
        assert snap["points_p1"] == 2 * (5 * 3 + 3 * 3 + 2 * 3)
        assert snap["points_p2"] == 3 * (5 * 3 + 3 * 3 + 2 * 3)
        assert snap["winner"] == "p2"

        events = events_of(app, match["id"])
        names = [name for _, name, _ in events]
        assert names.count("round_revealed") == 3 and names.count("ruling") == 6
        assert names[-1] == "match_ended"
        assert names.index("round_revealed") > names.index("ruling")
        reveals = [d for _, name, d in events if name == "round_revealed"]
        assert reveals[0]["token"] == "zarf" and reveals[0]["truth"].startswith("a holder")
        rulings = [d for _, name, d in events if name == "ruling"]
        assert rulings[0]["badges"] == ["accidental_truth"]
        assert rulings[2]["points"] == 0
        assert rulings[4]["badges"] == ["close_call"]
        assert rulings[5]["badges"] == ["near_miss"]
        assert all(h for h in caller.hidden_seen)
        assert caller.opponent_saw[0] == []
        assert caller.opponent_saw[1][0].startswith("round 1, prompt: zarf")
        assert "player1: a desert cloak" in caller.opponent_saw[1]

        replay = (await client.get(f"/replays/{match['id']}")).json()
        assert "lost a duel of Word for Word" in replay["share_text"]
        assert replay["transcript"][0]["host"]["badges"] == ["accidental_truth"]
        assert replay["transcript"][4]["host"]["badges"] == ["close_call"]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_house_writes_again_when_its_bluff_is_the_truth():
    caller = FakeCaller(
        rulings=[judge_response(), judge_response(truth_proximity="hit"), judge_response()],
        opponent_moves=["a cup holder", "a desert wind"],
    )
    app, manager, client = await run_app(caller)
    try:
        match = (
            await client.post(
                "/matches", json={"template_id": "word-for-word", "seed_token": "zarf"}
            )
        ).json()
        await settle(app)
        await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "r1", "expected_version": 0, "move_text": "a woollen cloak"},
        )
        await settle(app)
        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert snap["transcript"][1]["move_text"] == "a desert wind"
        assert "real meaning" in caller.opponent_saw[1][-1]
        assert snap["transcript"][1]["host"]["badges"] == []
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_featured_template_leads_the_list():
    app, manager, client = await run_app(FakeCaller(rulings=[], opponent_moves=[]))
    try:
        listed = (await client.get("/templates")).json()
        assert [t["slug"] for t in listed][:2] == ["then-i-am", "word-for-word"]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)
    settings = dataclasses.replace(
        load_settings(),
        database_url=os.environ["TEST_DATABASE_URL"],
        featured_template="word-for-word",
    )
    app = build_app(settings, FakeCaller(rulings=[], opponent_moves=[]))
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.get("/templates")).json()[0]["slug"] == "word-for-word"
    with pytest.raises(KeyError):
        build_app(
            dataclasses.replace(settings, featured_template="no-such-game"), FakeCaller([], [])
        )
