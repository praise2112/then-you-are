import asyncio
import dataclasses
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from arena_core.template import load_template
from arena_server.app import build_app
from arena_server.config import load_settings
from tests.conftest import FakeCaller, events_of, judge_response, run_app, settle

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL pointing at Postgres",
    ),
    pytest.mark.xdist_group("database"),
]


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
        assert [s["display_name"] for s in match["seats"]] == ["Echo", "The House"]
        assert match["your_seat"] == "p1" and match["kind"] == "house"

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
        assert ended["end_reason"] == ended["result_kind"] == "sudden_death"
        assert ended["winner"] == "p2"
        assert ended["coaching_line"] == "A geologist would have won."

        replay = await client.get(f"/replays/{match['id']}")
        assert replay.status_code == 200
        assert "→" in replay.json()["share_text"]
        assert replay.json()["result_kind"] == "sudden_death"
        shell = await client.get(f"/r/{match['id']}")
        assert 'property="og:title"' in shell.text
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_event_stream_of_a_missing_match_is_a_404_and_opens_no_stream():
    app, manager, client = await run_app(FakeCaller(rulings=[], opponent_moves=[]))
    try:
        assert (await client.get("/matches/nosuchmatch/events")).status_code == 404
        assert "nosuchmatch" not in app.state.bus.streams
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_event_stream_of_an_ended_match_is_a_404_and_opens_no_stream(monkeypatch):
    monkeypatch.setattr("arena_server.matches.STREAM_LINGER_S", 0)
    app, manager, client = await run_app(FakeCaller(rulings=[], opponent_moves=[]))
    try:
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        resigned = await client.post(
            f"/matches/{match['id']}/resign", json={"action_id": "r", "expected_version": 0}
        )
        assert resigned.status_code == 202
        await settle(app)
        await asyncio.sleep(0.01)
        assert match["id"] not in app.state.bus.streams
        assert (await client.get(f"/matches/{match['id']}/events")).status_code == 404
        assert match["id"] not in app.state.bus.streams
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_seating_the_house_wakes_its_server_before_the_first_move():
    caller = FakeCaller(rulings=[], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        await client.post("/matches", json={"template_id": "then-i-am"})
        await settle(app)
        assert caller.wakes == 1
        assert caller.opponent_saw == []
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_flash_takes_the_house_seat_for_the_rest_of_the_match_when_the_house_is_down():
    caller = FakeCaller(
        rulings=[],
        opponent_moves=["I am a well, bucket-swallowing.", "I am a pump, well-draining."],
        house_down=True,
    )
    settings = dataclasses.replace(
        load_settings(),
        database_url=os.environ["TEST_DATABASE_URL"],
        curator_token="shh",
        opponent_ref="student-local",
    )
    app = build_app(settings, caller)
    manager = LifespanManager(app)
    await manager.__aenter__()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        for version, move in [(0, "I am a draft, flame-killing."), (2, "I am a lid, pot-sealing.")]:
            await client.post(
                f"/matches/{match['id']}/moves",
                json={"action_id": move, "expected_version": version, "move_text": move},
            )
            await settle(app)
        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert caller.stand_in_moves == 2
        assert [(t["actor"], t["played_by"]) for t in snap["transcript"]] == [
            ("p1", None),
            ("p2", "DeepSeek Flash"),
            ("p1", None),
            ("p2", "DeepSeek Flash"),
        ]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_judge_that_never_rules_hands_the_move_back(monkeypatch):
    monkeypatch.setattr("arena_server.judging.JUDGE_GIVE_UP_S", 0)
    monkeypatch.setattr("arena_server.judging.PAUSE_BACKOFF_S", (0,))
    caller = FakeCaller(rulings=[None, None, None], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        move = "I am a draft, flame-killing."
        await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "g1", "expected_version": 0, "move_text": move},
        )
        await settle(app)
        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert (snap["status"], snap["to_move"], snap["transcript"]) == ("active", "p1", [])
        assert caller.judged == [move]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


class BrokenHouse(FakeCaller):
    """A House whose first answer breaks with an error no caller expects."""

    def __init__(self, rulings: list, opponent_moves: list[str]):
        super().__init__(rulings, opponent_moves)
        self.broken = True

    def opponent_stream(
        self, template, seat, card, transcript, hidden="", slot=None, spec=None
    ) -> AsyncIterator[str]:
        if not self.broken:
            return super().opponent_stream(template, seat, card, transcript, hidden, slot, spec)
        self.broken = False

        async def breaks() -> AsyncIterator[str]:
            raise RuntimeError("the House broke")
            yield ""

        return breaks()


@pytest.mark.anyio
async def test_a_house_turn_that_breaks_is_forfeited_and_play_goes_on():
    app, manager, client = await run_app(BrokenHouse([], ["I am a key, lock-turning."]))
    try:
        match_id = (await client.post("/matches", json={"template_id": "then-i-am"})).json()["id"]
        moved = await client.post(
            f"/matches/{match_id}/moves",
            json={"action_id": "a1", "expected_version": 0, "move_text": "I am rust, hinge-eat."},
        )
        assert moved.status_code == 202
        await settle(app)
        snap = (await client.get(f"/matches/{match_id}")).json()
        assert (snap["status"], snap["to_move"]) == ("active", "p1")
        assert [(t["actor"], t["outcome"]) for t in snap["transcript"]] == [
            ("p1", "accept"),
            ("p2", "forfeit"),
        ]
        again = await client.post(
            f"/matches/{match_id}/moves",
            json={
                "action_id": "a2",
                "expected_version": snap["state_version"],
                "move_text": "I am a pick, lock-opening.",
            },
        )
        assert again.status_code == 202
        await settle(app)
        snap = (await client.get(f"/matches/{match_id}")).json()
        assert [t["actor"] for t in snap["transcript"]] == ["p1", "p2", "p1", "p2"]
        assert snap["to_move"] == "p1"
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_snapshot_never_shows_a_turn_newer_than_its_version(monkeypatch):
    import psycopg

    app, manager, client = await run_app(FakeCaller([], []))
    match_id = (await client.post("/matches", json={"template_id": "then-i-am"})).json()["id"]
    await settle(app)
    execute = psycopg.AsyncConnection.execute
    landed = False

    async def land_a_turn_between_reads(self, query: Any, *args, **kwargs):
        # A forfeit commits after the match row is read and before its turns are.
        nonlocal landed
        if not landed and str(query).startswith("select t.*"):
            landed = True
            async with await psycopg.AsyncConnection.connect(
                os.environ["TEST_DATABASE_URL"], autocommit=True
            ) as other:
                await other.execute(
                    "update matches set state_version = state_version + 1 where id = %s",
                    (match_id,),
                )
                await other.execute(
                    "insert into turns (match_id, seq, actor, move_text, outcome, "
                    "layer1_result, round_n) values (%s, 1, 'p1', '', 'forfeit', 'forfeit', 1)",
                    (match_id,),
                )
        return await execute(self, query, *args, **kwargs)

    try:
        monkeypatch.setattr(psycopg.AsyncConnection, "execute", land_a_turn_between_reads)
        snap = (await client.get(f"/matches/{match_id}")).json()
        assert landed
        assert (snap["state_version"], len(snap["transcript"])) == (0, 0)
        snap = (await client.get(f"/matches/{match_id}")).json()
        assert (snap["state_version"], len(snap["transcript"])) == (1, 1)
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
async def test_judge_outage_pauses_then_resumes_with_the_same_move(monkeypatch):
    monkeypatch.setattr("arena_server.judging.PAUSE_BACKOFF_S", (0,))
    caller = FakeCaller(rulings=[None, judge_response(verdict="fail")], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
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
        stranger = httpx.AsyncClient(transport=client._transport, base_url="http://test")
        stray = await stranger.post(f"/matches/{match['id']}/turns/1/disagree")
        await stranger.aclose()
        assert stray.status_code == 403
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
        listed = (await client.post("/matches", json={"template_id": "word-for-word"})).json()
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

        stranger = httpx.AsyncClient(transport=client._transport, base_url="http://test")
        listed = (await stranger.get("/replays?sort=newest")).json()
        assert listed == [(await stranger.get(f"/replays/{r['id']}")).json() for r in listed]
        await stranger.aclose()
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_word_duel_holds_the_house_bluff_until_the_reveal():
    # The House is written and judged as each round opens, before the player's move, so the
    # scripted rulings run House, player, House, player, House, player.
    caller = FakeCaller(
        rulings=[
            judge_response(),
            judge_response(truth_proximity="hit"),
            judge_response(),
            judge_response(verdict="fail"),
            judge_response(truth_proximity="near"),
            judge_response(confidence="coin_flip"),
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

        # Round 1: the player hits the truth, so the House's table has one entry and the
        # House owes no call; the player calls between the House bluff and the truth.
        # Rounds 2 and 3: the player calls the bluff, then the truth.
        for n, (version, bluff, call) in enumerate(
            [(0, "a desert cloak", "truth"), (3, "to shell peas", "bluff"), (6, "a knot", "truth")],
            start=1,
        ):
            posted = await client.post(
                f"/matches/{match['id']}/moves",
                json={"action_id": f"r{n}", "expected_version": version, "move_text": bluff},
            )
            assert posted.status_code == 202
            await settle(app)
            snap = (await client.get(f"/matches/{match['id']}")).json()
            assert snap["phase"] == "guess" and snap["state_version"] == version + 2
            assert all(t["round_n"] != n for t in snap["transcript"])
            table = snap["rounds"][-1]
            assert table["truth"] is None and len(table["options"]) == 2
            texts = {o["text"] for o in table["options"]}
            assert bluff not in texts and len({o["key"] for o in table["options"]}) == 2
            card = load_template("word-for-word").seed_named(table["token"])
            assert card is not None
            truth = card.hidden
            picked = next(o for o in table["options"] if (o["text"] == truth) == (call == "truth"))
            early = await client.post(
                f"/matches/{match['id']}/moves",
                json={"action_id": f"x{n}", "expected_version": version + 2, "move_text": "no"},
            )
            assert early.status_code == 409
            called = await client.post(
                f"/matches/{match['id']}/guesses",
                json={"action_id": f"g{n}", "expected_version": version + 2, "key": picked["key"]},
            )
            assert called.status_code == 202
            again = await client.post(
                f"/matches/{match['id']}/guesses",
                json={"action_id": f"g{n}", "expected_version": version + 2, "key": picked["key"]},
            )
            assert again.status_code == 202
            await settle(app)

        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert snap["status"] == "ended" and snap["end_reason"] == "rounds_complete"
        assert snap["phase"] == "write"
        assert [t["round_n"] for t in snap["transcript"]] == [1, 1, 2, 2, 3, 3]
        # The House's answer is recorded when it is judged, before the player's.
        assert [t["actor"] for t in snap["transcript"]] == ["p2", "p1"] * 3
        assert snap["transcript"][0]["move_text"] == "a cup holder"
        assert all(r["truth"] for r in snap["rounds"]) and len(snap["rounds"]) == 3
        assert [r["guesses"] for r in snap["rounds"]] == [
            [{"actor": "p1", "picked": "truth", "points": 10, "awarded_to": "p1"}],
            [{"actor": "p1", "picked": "p2", "points": 10, "awarded_to": "p2"}],
            [{"actor": "p1", "picked": "truth", "points": 10, "awarded_to": "p1"}],
        ]
        bluff_points = 5 * 3 + 3 * 3 + 2 * 3
        points = {s["seat"]: s["points"] for s in snap["seats"]}
        assert points == {"p1": 2 * bluff_points + 20, "p2": 3 * bluff_points + 10}
        assert snap["winner"] == "p2"

        events = events_of(app, match["id"])
        names = [name for _, name, _ in events]
        assert names.count("guess_opened") == 3
        assert names.count("round_revealed") == 3 and names.count("ruling") == 6
        assert names[-1] == "match_ended"
        assert names.index("guess_opened") < names.index("ruling") < names.index("round_revealed")
        opened = [d for _, name, d in events if name == "guess_opened"]
        assert opened[0] == {"round_n": 1, "state_version": 2}
        reveals = [d for _, name, d in events if name == "round_revealed"]
        assert reveals[0]["token"] == "zarf" and reveals[0]["truth"].startswith("a holder")
        assert reveals[0]["guesses"][0]["picked"] == "truth"
        assert reveals[0]["totals"] == {"p1": bluff_points + 10, "p2": bluff_points}
        rulings = [d for _, name, d in events if name == "ruling"]
        assert rulings[1]["host"]["badges"] == ["accidental_truth"]
        assert rulings[3]["points"] == 0
        assert rulings[4]["host"]["badges"] == ["near_miss"]
        assert rulings[5]["host"]["badges"] == ["close_call"]
        assert all(h for h in caller.hidden_seen)
        assert caller.opponent_saw[0] == []
        assert caller.opponent_hidden[0].startswith("a holder")
        assert caller.opponent_saw[1][0].startswith("round 1, prompt: zarf")
        assert "player1: a desert cloak" in caller.opponent_saw[1]

        replay = (await client.get(f"/replays/{match['id']}")).json()
        assert "lost a duel of Word for Word" in replay["share_text"]
        assert replay["result_kind"] == "points"
        assert replay["transcript"][1]["host"]["badges"] == ["accidental_truth"]
        assert replay["transcript"][5]["host"]["badges"] == ["close_call"]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_house_writes_again_when_its_bluff_is_the_truth():
    caller = FakeCaller(
        rulings=[judge_response(truth_proximity="hit"), judge_response(), judge_response()],
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
        table = snap["rounds"][0]["options"]
        assert "a desert wind" in {o["text"] for o in table} and len(table) == 2
        assert len(caller.opponent_saw) == 2 and all(caller.opponent_hidden)
        await client.post(
            f"/matches/{match['id']}/guesses",
            json={"action_id": "g1", "expected_version": 2, "key": table[0]["key"]},
        )
        snap = (await client.get(f"/matches/{match['id']}")).json()
        assert snap["transcript"][0]["move_text"] == "a desert wind"
        assert snap["transcript"][0]["host"]["badges"] == []
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_featured_template_leads_the_list():
    app, manager, client = await run_app(FakeCaller(rulings=[], opponent_moves=[]))
    try:
        listed = (await client.get("/templates")).json()
        assert [t["slug"] for t in listed][0] == "then-i-am"
        assert [t["slug"] for t in listed if t["featured"]] == ["then-i-am"]
        single = (await client.get("/templates/domino")).json()
        assert single["featured"] is False and single["medallions"] is False
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


@pytest.mark.anyio
async def test_the_games_may_be_cached_for_five_minutes_but_a_missing_one_is_not():
    app, manager, client = await run_app(FakeCaller(rulings=[], opponent_moves=[]))
    try:
        for path in ("/templates", "/templates/domino"):
            response = await client.get(path)
            assert response.headers["cache-control"] == "public, max-age=300"
        assert "cache-control" not in (await client.get("/templates/nope")).headers
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_ranked_player_count_may_be_cached_for_a_minute():
    app, manager, client = await run_app(FakeCaller(rulings=[], opponent_moves=[]))
    try:
        response = await client.get("/leaderboard/players")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=60"
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_command_for_a_missing_or_finished_match_keeps_no_lock():
    app, manager, client = await run_app(FakeCaller(rulings=[], opponent_moves=[]))
    service = app.state.service
    try:
        duel = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        resign = {"action_id": "r", "expected_version": 0}
        assert (await client.post(f"/matches/{duel['id']}/resign", json=resign)).status_code == 202
        await settle(app)
        late = await client.post(f"/matches/{duel['id']}/resign", json={**resign, "action_id": "s"})
        assert late.status_code == 409
        missing = await client.post("/matches/nosuchmatch/resign", json=resign)
        assert missing.status_code == 404
        assert "nosuchmatch" not in service.locks and duel["id"] not in service.locks
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_refused_judge_bill_backs_off_and_flags_the_health_check(monkeypatch):
    import arena_server.judging as judging

    monkeypatch.setattr(judging, "PAUSE_BACKOFF_S", (5,))
    monkeypatch.setattr(judging, "BILLING_RETRY_S", 0.2)
    caller = FakeCaller(rulings=[402, judge_response(verdict="fail")], opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        assert (await client.get("/healthz")).json()["judge"] == "ok"
        match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        started = time.monotonic()
        await client.post(
            f"/matches/{match['id']}/moves",
            json={"action_id": "c1", "expected_version": 0, "move_text": "I am a whisper."},
        )
        await asyncio.sleep(0.05)
        assert (await client.get("/healthz")).json()["judge"] == "judge provider answered HTTP 402"
        await settle(app)
        assert time.monotonic() - started < 2
        names = [name for _, name, _ in events_of(app, match["id"])]
        assert names == ["judge_started", "judge_paused", "judge_resumed", "ruling", "match_ended"]
        assert (await client.get("/healthz")).json()["judge"] == "ok"
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_an_idle_match_is_abandoned_and_its_pending_judge_call_stops(monkeypatch):
    import arena_server.judging as judging

    monkeypatch.setattr(judging, "PAUSE_BACKOFF_S", (0.2,))
    caller = FakeCaller(rulings=[None] * 50, opponent_moves=[])
    app, manager, client = await run_app(caller)
    try:
        stale = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        await client.post(
            f"/matches/{stale['id']}/moves",
            json={"action_id": "c1", "expected_version": 0, "move_text": "I am a whisper."},
        )
        await asyncio.sleep(0.05)
        async with app.state.service.pool.connection() as conn:
            await conn.execute(
                "update matches set updated_at = now() - interval '25 hours' where id = %s",
                (stale["id"],),
            )
        assert stale["id"] in await app.state.service.close_abandoned()
        await settle(app)
        fresh = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        assert fresh["id"] != stale["id"]

        snap = (await client.get(f"/matches/{stale['id']}")).json()
        assert (snap["status"], snap["end_reason"]) == ("abandoned", "abandoned")
        assert (await client.get(f"/matches/{fresh['id']}")).json()["status"] == "active"
        stuck = await client.post(
            f"/matches/{stale['id']}/moves",
            json={"action_id": "c2", "expected_version": 1, "move_text": "I am rain."},
        )
        assert stuck.status_code == 409
        assert len(caller.judged) <= 4
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_blocked_names_are_refused_with_a_plain_reason():
    app, manager, client = await run_app(FakeCaller([], []))
    try:
        for name in ["Sh1thead", "The House", "admin"]:
            refused = await client.post(
                "/matches", json={"template_id": "then-i-am", "stage_name": name}
            )
            assert refused.status_code == 422, name
            assert (
                refused.json()["detail"] == "That name will not do on a public stage. Pick another."
            )
        refused = await client.put("/sessions/me", json={"stage_name": "Ass Kicker"})
        assert refused.status_code == 422
        fine = await client.put("/sessions/me", json={"stage_name": "Scunthorpe"})
        assert fine.json()["stage_name"] == "Scunthorpe"
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_starting_a_game_with_a_duel_open_resumes_it():
    app, manager, client = await run_app(FakeCaller([], []))
    try:
        first = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        again = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
        assert again["id"] == first["id"]
        other = (await client.post("/matches", json={"template_id": "word-for-word"})).json()
        assert other["id"] != first["id"]
        me = (await client.get("/sessions/me")).json()
        assert [d["id"] for d in me["open_duels"]] == [first["id"], other["id"]]
        duel, words = me["open_duels"]
        assert (duel["title"], duel["mode"]) == ("Then I Am", "escalation")
        assert (duel["round_n"], duel["waiting_for"]) == (1, None)
        assert duel["card"] == first["seed_token"]
        assert (words["round_n"], words["rounds_budget"]) == (1, 3)
        assert words["card"] == other["seed_token"]
        assert not duel["your_turn"] and not words["your_turn"]
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_each_house_conversation_keeps_a_llama_server_slot_until_its_match_ends():
    caller = FakeCaller(rulings=[], opponent_moves=[])
    settings = dataclasses.replace(
        load_settings(),
        database_url=os.environ["TEST_DATABASE_URL"],
        curator_token="shh",
        opponent_ref="student-local",
    )
    app = build_app(settings, caller)
    manager = LifespanManager(app)
    await manager.__aenter__()
    transport = httpx.ASGITransport(app=app)
    clients = [httpx.AsyncClient(transport=transport, base_url="http://test") for _ in range(3)]

    async def play(client: httpx.AsyncClient, match_id: str, version: int, move: str) -> None:
        await client.post(
            f"/matches/{match_id}/moves",
            json={"action_id": move, "expected_version": version, "move_text": move},
        )
        await settle(app)

    try:
        ids = []
        for client, move in zip(
            clients,
            [
                "I am a draft, flame-killing.",
                "I am a lid, pot-sealing.",
                "I am a fan, heat-chasing.",
            ],
            strict=True,
        ):
            match = (await client.post("/matches", json={"template_id": "then-i-am"})).json()
            ids.append(match["id"])
            await play(client, match["id"], 0, move)
        assert caller.opponent_slots == [0, 1, None]
        resigned = await clients[0].post(
            f"/matches/{ids[0]}/resign", json={"action_id": "r1", "expected_version": 2}
        )
        assert resigned.status_code == 202
        await settle(app)
        await play(clients[2], ids[2], 2, "I am a hose, fire-drowning.")
        assert caller.opponent_slots[-1] == 0
    finally:
        for client in clients:
            await client.aclose()
        await manager.__aexit__(None, None, None)


class StuckHouse(FakeCaller):
    """A House that starts an answer and never finishes, as when the server stops mid-call."""

    def opponent_stream(
        self, template, seat, card, transcript, hidden="", slot=None, spec=None
    ) -> AsyncIterator[str]:
        async def hang() -> AsyncIterator[str]:
            await asyncio.Event().wait()
            yield ""

        return hang()


@pytest.mark.anyio
async def test_a_restart_plays_every_house_answer_still_owed():
    app, manager, ana = await run_app(StuckHouse([], []))
    duel = (await ana.post("/matches", json={"template_id": "then-i-am"})).json()
    words = (await ana.post("/matches", json={"template_id": "word-for-word"})).json()
    await ana.post(
        f"/matches/{duel['id']}/moves",
        json={"action_id": "a1", "expected_version": 0, "move_text": "I am rust, hinge-eating."},
    )
    async with asyncio.timeout(5):
        while "ruling" not in [name for _, name, _ in events_of(app, duel["id"])]:
            await asyncio.sleep(0.01)
    for task in list(app.state.service.tasks):
        task.cancel()
    await settle(app)
    cookies = ana.cookies
    await ana.aclose()
    await manager.__aexit__(None, None, None)

    app, manager, ana = await run_app(FakeCaller([], ["I am a key, lock-turning."]))
    ana.cookies = cookies
    spectator = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def house_answered() -> bool:
        return (await spectator.get(f"/matches/{words['id']}")).json()["seats"][1]["answered"]

    try:
        await settle(app)
        state = (await ana.get(f"/matches/{duel['id']}")).json()
        assert [t["actor"] for t in state["transcript"]] == ["p1", "p2"]
        assert state["to_move"] == "p1" and state["status"] == "active"
        # The word duel's House answers at boot, before anyone opens the match.
        assert await house_answered()
        for match_id, version in ((duel["id"], 2), (words["id"], 0)):
            await ana.post(
                f"/matches/{match_id}/resign", json={"action_id": "r", "expected_version": version}
            )
        await settle(app)
    finally:
        await spectator.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


async def get_texts(settings, *paths: str) -> list[tuple[int, str]]:
    app = build_app(settings, FakeCaller(rulings=[], opponent_moves=[]))
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        return [((r := await c.get(path)).status_code, r.text) for path in paths]


@pytest.mark.anyio
async def test_the_writeup_link_previews_with_its_own_title_and_image(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(
        '<html><head><meta property="og:image" content="https://site/og-image.png" />'
        "</head><body></body></html>"
    )
    settings = dataclasses.replace(
        load_settings(),
        database_url=os.environ["TEST_DATABASE_URL"],
        frontend_dist=tmp_path,
        public_base_url="https://example.org",
    )
    [(status, page)] = await get_texts(settings, "/how-it-was-built")
    assert status == 200
    assert '<meta property="og:title" content="How the models in Then You Are were built">' in page
    assert 'content="https://example.org/how-it-was-built"' in page
    assert page.count('property="og:image"') == 1
    assert 'content="https://example.org/og-image.png"' in page
