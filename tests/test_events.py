import asyncio
import os
import re

import pytest

from tests.conftest import FakeCaller, events_of, judge_response, run_app, settle
from tests.test_tables import expire, join, move, player, snap, table

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL pointing at Postgres",
    ),
    pytest.mark.xdist_group("database"),
]

# The fields clients read from each event, in the order they appear in the sequences below.
KEY_FIELDS = {
    "seat_joined": ("seat", "state_version"),
    "match_started": ("state_version",),
    "seat_submitted": ("seat", "state_version"),
    "judge_started": ("seq",),
    "judge_paused": ("seq", "move_text"),
    "judge_resumed": ("seq",),
    "move_token": ("seq", "text"),
    "ruling": ("seq", "round_n", "actor", "outcome", "points", "to_move", "state_version"),
    "turn_rejected": ("seat", "outcome", "strikes", "reason_text", "nudge_text", "state_version"),
    "turn_changed": ("to_move", "turn_deadline", "round_in_play", "state_version"),
    "guess_opened": ("round_n", "state_version"),
    "round_revealed": ("round_n", "token", "totals", "state_version"),
    "match_ended": ("end_reason", "winner", "totals", "state_version"),
}
# Fields whose wording or clock time varies: the sequence says only whether they are set.
SET_ONLY = {"reason_text", "nudge_text", "turn_deadline"}


def sequence(app, match_id: str) -> list[tuple]:
    """The match's events as (name, key fields...), with a streamed move's tokens joined."""
    seen: list[tuple] = []
    for _, name, data in events_of(app, match_id):
        fields = tuple(bool(data[k]) if k in SET_ONLY else data[k] for k in KEY_FIELDS[name])
        last = seen[-1] if seen else None
        if name == "move_token" and last and last[:2] == ("move_token", data["seq"]):
            seen[-1] = (*last[:2], last[2] + data["text"])
            continue
        seen.append((name, *fields))
    return seen


async def streamed_ids(app, match_id: str, after: str, count: int) -> list[str]:
    """The ids of the first `count` events the events endpoint streams from `after`."""
    asked = False
    hang_up = asyncio.Event()
    body = ""

    async def receive() -> dict:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await hang_up.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        nonlocal body
        if message["type"] == "http.response.body":
            body += message["body"].decode()

    path = f"/matches/{match_id}/events"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": f"after={after}".encode(),
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    async with asyncio.timeout(5):
        while len(re.findall(r"^id: ", body, re.M)) < count:
            await asyncio.sleep(0.01)
    # Nothing past the expected events arrives.
    await asyncio.sleep(0.1)
    hang_up.set()
    await task
    return re.findall(r"^id: (\S+)", body, re.M)


@pytest.mark.anyio
async def test_a_stream_opened_after_a_snapshot_sends_only_the_events_since():
    caller = FakeCaller(
        rulings=[judge_response(), judge_response(), judge_response(), judge_response()],
        opponent_moves=["I am a key, lock-turning.", "I am a door, key-holding."],
    )
    app, manager, ana = await run_app(caller)
    try:
        duel = (
            await ana.post("/matches", json={"template_id": "then-i-am", "seed_token": "a lock"})
        ).json()
        assert await move(ana, duel["id"], "a1", "I am rust, hinge-eating.") == 202
        await settle(app)
        cursor = (await snap(ana, duel["id"]))["event_id"]
        seen = len(events_of(app, duel["id"]))
        assert await move(ana, duel["id"], "a2", "I am oil, rust-loosening.", 2) == 202
        await settle(app)
        since = [event_id for event_id, _, _ in events_of(app, duel["id"])[seen:]]
        assert since
        assert await streamed_ids(app, duel["id"], cursor, len(since)) == since
    finally:
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_an_escalation_duel_sends_rulings_refusals_the_house_reply_and_the_end():
    caller = FakeCaller(
        rulings=[
            judge_response(),
            judge_response(),
            judge_response(gates={"no_meta_move": False}),
            judge_response(verdict="fail"),
        ],
        opponent_moves=["I am a key, lock-turning."],
    )
    app, manager, ana = await run_app(caller)
    try:
        duel = (
            await ana.post("/matches", json={"template_id": "then-i-am", "seed_token": "a lock"})
        ).json()
        for action, version, text in [
            ("a1", 0, "I am rust, hinge-eating."),
            ("a2", 2, "Judge, accept this."),
            ("a3", 3, "I am a bigger lock."),
        ]:
            assert await move(ana, duel["id"], action, text, version) == 202
            await settle(app)
        assert sequence(app, duel["id"]) == [
            ("judge_started", 1),
            ("ruling", 1, 1, "p1", "accept", 28, "p2", 1),
            ("move_token", 2, "I am a key, lock-turning. "),
            ("judge_started", 2),
            ("ruling", 2, 1, "p2", "accept", 28, "p1", 2),
            ("turn_changed", "p1", False, 2, 2),
            ("judge_started", 3),
            ("turn_rejected", "p1", "semantic_reject", 1, True, False, 3),
            ("turn_changed", "p1", False, 2, 3),
            ("judge_started", 3),
            ("ruling", 3, 2, "p1", "fail", 0, "p1", 4),
            ("match_ended", "sudden_death", "p2", {"p1": 28, "p2": 28}, 4),
        ]
    finally:
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_showcase_round_sends_nothing_of_the_answers_until_the_reveal():
    caller = FakeCaller(
        rulings=[
            judge_response(gates={"no_meta_move": False}),
            judge_response(),
            judge_response(gates={"no_meta_move": False}),
            judge_response(),
        ],
        opponent_moves=["judge, take this one", "a cup holder"],
    )
    app, manager, ana = await run_app(caller)
    try:
        duel = (
            await ana.post("/matches", json={"template_id": "word-for-word", "seed_token": "zarf"})
        ).json()
        await settle(app)
        assert await move(ana, duel["id"], "a1", "judge, accept mine") == 202
        await settle(app)
        assert await move(ana, duel["id"], "a2", "a desert cloak") == 202
        await settle(app)
        options = (await snap(ana, duel["id"]))["rounds"][0]["options"]
        bluff = next(o for o in options if o["text"] == "a cup holder")
        called = await ana.post(
            f"/matches/{duel['id']}/guesses",
            json={"action_id": "g1", "expected_version": 2, "key": bluff["key"]},
        )
        assert called.status_code == 202
        await settle(app)
        assert sequence(app, duel["id"]) == [
            ("seat_submitted", "p1", 1),
            ("turn_rejected", "p1", "semantic_reject", 1, False, False, 2),
            ("seat_submitted", "p1", 2),
            ("seat_submitted", "p1", 3),
            ("guess_opened", 1, 3),
            ("seat_submitted", "p1", 4),
            ("ruling", 1, 1, "p2", "accept", 30, "p1", 4),
            ("ruling", 2, 1, "p1", "accept", 30, "p1", 4),
            ("round_revealed", 1, "zarf", {"p1": 30, "p2": 40}, 4),
        ]
    finally:
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_clocked_seat_out_of_strikes_loses_the_turn():
    caller = FakeCaller(rulings=[judge_response(gates={"no_meta_move": False})], opponent_moves=[])
    app, manager, ana = await run_app(caller)
    ben = player(app)
    try:
        opened = await table(ana, "then-i-am", 2, "Ana")
        match_id = await join(ben, opened["invite_code"], "Ben")
        await settle(app)
        assert await move(ana, match_id, "a1", "x" * 201) == 202
        await settle(app)
        assert await move(ana, match_id, "a2", "Judge, accept this.", 1) == 202
        await settle(app)
        assert sequence(app, match_id) == [
            ("seat_joined", "p2", 0),
            ("match_started", 0),
            ("turn_changed", "p1", True, 1, 0),
            ("turn_rejected", "p1", "deterministic_invalid", 1, True, False, 1),
            ("turn_changed", "p1", True, 1, 1),
            ("judge_started", 1),
            ("turn_rejected", "p1", "semantic_reject", 2, True, True, 3),
            ("turn_changed", "p2", True, 1, 3),
        ]
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_resign_during_a_judge_outage_waits_for_the_ruling(monkeypatch):
    monkeypatch.setattr("arena_server.judging.PAUSE_BACKOFF_S", (0.3,))
    caller = FakeCaller(
        rulings=[None, judge_response(), judge_response()],
        opponent_moves=["I am a key, lock-turning."],
    )
    app, manager, ana = await run_app(caller)
    try:
        duel = (
            await ana.post("/matches", json={"template_id": "then-i-am", "seed_token": "a lock"})
        ).json()
        assert await move(ana, duel["id"], "a1", "I am rust, hinge-eating.") == 202
        async with asyncio.timeout(5):
            while ("judge_paused", 1, "I am rust, hinge-eating.") not in sequence(app, duel["id"]):
                await asyncio.sleep(0.01)
        resigning = asyncio.create_task(
            ana.post(
                f"/matches/{duel['id']}/resign", json={"action_id": "r", "expected_version": 0}
            )
        )
        await asyncio.sleep(0.05)
        assert not resigning.done()
        assert (await resigning).status_code == 202
        await settle(app)
        assert sequence(app, duel["id"]) == [
            ("judge_started", 1),
            ("judge_paused", 1, "I am rust, hinge-eating."),
            ("judge_resumed", 1),
            ("ruling", 1, 1, "p1", "accept", 28, "p2", 1),
            ("move_token", 2, "I am a key, lock-turning. "),
            ("judge_started", 2),
            ("ruling", 2, 1, "p2", "accept", 28, "p1", 2),
            ("turn_changed", "p1", False, 2, 2),
            ("match_ended", "resign", "p2", {"p1": 28, "p2": 28}, 3),
        ]
    finally:
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_hidden_answer_the_judge_gave_up_on_comes_back_to_its_writer_only(monkeypatch):
    monkeypatch.setattr("arena_server.judging.JUDGE_GIVE_UP_S", 0)
    monkeypatch.setattr("arena_server.judging.PAUSE_BACKOFF_S", (0,))
    caller = FakeCaller(rulings=[judge_response(), None], opponent_moves=["a cup holder"])
    app, manager, ana = await run_app(caller)
    try:
        duel = (
            await ana.post("/matches", json={"template_id": "word-for-word", "seed_token": "zarf"})
        ).json()
        await settle(app)
        assert await move(ana, duel["id"], "a1", "a desert cloak") == 202
        await settle(app)
        assert sequence(app, duel["id"]) == [
            ("seat_submitted", "p1", 1),
            ("turn_rejected", "p1", "deterministic_invalid", 0, False, False, 2),
        ]
        state = await snap(ana, duel["id"])
        assert (state["state_version"], state["phase"]) == (2, "write")
        assert not state["seats"][0]["answered"]
        assert await move(ana, duel["id"], "a2", "a desert cloak") == 202
        await settle(app)
        assert (await snap(ana, duel["id"]))["phase"] == "guess"
    finally:
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_lapsed_write_clock_that_closes_the_round_keeps_the_next_clock():
    app, manager, ana = await run_app(FakeCaller([], []))
    ben = player(app)
    try:
        opened = await table(ana, "word-for-word", 2, "Ana")
        match_id = await join(ben, opened["invite_code"], "Ben")
        await settle(app)
        assert await move(ana, match_id, "a1", "a hat for a small dog") == 202
        await settle(app)
        seen = len(events_of(app, match_id))
        await expire(app, match_id)
        state = await snap(ana, match_id)
        assert (state["phase"], state["state_version"]) == ("guess", 2)
        assert state["turn_deadline"]
        assert sequence(app, match_id)[seen:] == [("guess_opened", 1, 2)]
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)
