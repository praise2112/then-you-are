import json
import os

import httpx
import pytest

from tests.conftest import FakeCaller, events_of, run_app, settle

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL pointing at Postgres",
    ),
    pytest.mark.xdist_group("database"),
]


def player(app) -> httpx.AsyncClient:
    """Another browser: its own cookie jar against the same app."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def table(client, template_id: str, seats: int, name: str) -> dict:
    created = await client.post(
        "/matches",
        json={"template_id": template_id, "kind": "friends", "seats": seats, "stage_name": name},
    )
    assert created.status_code == 201
    return created.json()


async def join(client, code: str, name: str) -> str:
    joined = await client.post("/tables/join", json={"invite_code": code, "stage_name": name})
    assert joined.status_code == 200
    return joined.json()["match_id"]


async def move(client, match_id: str, action: str, text: str, version: int = 0) -> int:
    posted = await client.post(
        f"/matches/{match_id}/moves",
        json={"action_id": action, "expected_version": version, "move_text": text},
    )
    return posted.status_code


async def snap(client, match_id: str) -> dict:
    return (await client.get(f"/matches/{match_id}")).json()


async def expire(app, match_id: str) -> None:
    async with app.state.service.pool.connection() as conn:
        await conn.execute(
            "update matches set turn_deadline = now() - interval '1 second' where id = %s",
            (match_id,),
        )
    assert match_id in await app.state.service.expire_clocks()
    await settle(app)


@pytest.mark.anyio
async def test_a_friend_joins_by_invite_and_the_two_take_turns_on_a_clock():
    app, manager, ana = await run_app(FakeCaller([], []))
    ben, spectator = player(app), player(app)
    try:
        opened = await table(ana, "then-i-am", 2, "Ana")
        assert (opened["status"], opened["kind"], opened["your_seat"]) == ("open", "friends", "p1")
        assert opened["invite_code"] and opened["seats_wanted"] == 2
        assert await move(ana, opened["id"], "early", "I am rain.") == 409

        match_id = await join(ben, opened["invite_code"], "Ben")
        assert match_id == opened["id"]
        assert await join(ben, opened["invite_code"], "Ben") == match_id
        await settle(app)
        started = await snap(ben, match_id)
        assert started["status"] == "active" and started["your_seat"] == "p2"
        assert [s["display_name"] for s in started["seats"]] == ["Ana", "Ben"]
        assert started["turn_deadline"] and started["invite_code"] is None
        assert (await snap(spectator, match_id))["your_seat"] is None

        assert await move(ben, match_id, "b0", "I am rain.") == 409
        assert await move(ana, match_id, "a1", "I am rain, rock-wearing.") == 202
        await settle(app)
        after = await snap(ben, match_id)
        assert after["to_move"] == "p2" and after["turn_deadline"]
        assert await move(ben, match_id, "b1", "I am a sponge, rain-drinking.", 1) == 202
        await settle(app)
        assert (await snap(ana, match_id))["to_move"] == "p1"
        names = [name for _, name, _ in events_of(app, match_id)]
        assert names[:2] == ["seat_joined", "match_started"]
        assert "turn_changed" in names

        crowd = player(app)
        late = await crowd.post("/tables/join", json={"invite_code": opened["invite_code"]})
        assert late.status_code == 409
        await crowd.aclose()
    finally:
        await ben.aclose()
        await spectator.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_creator_fills_a_seat_with_the_house_and_it_plays_in_turn():
    caller = FakeCaller([], ["I am a dam, river-stopping.", "I am a flood, dam-breaking."])
    app, manager, ana = await run_app(caller)
    ben = player(app)
    try:
        opened = await table(ana, "then-i-am", 3, "Ana")
        await join(ben, opened["invite_code"], "Ben")
        refused = await ben.post(f"/matches/{opened['id']}/seats/house")
        assert refused.status_code == 403
        assert (await ana.post(f"/matches/{opened['id']}/seats/house")).status_code == 204
        await settle(app)
        started = await snap(ana, opened["id"])
        assert [(s["seat"], s["kind"]) for s in started["seats"]] == [
            ("p1", "human"),
            ("p2", "human"),
            ("p3", "model"),
        ]
        assert await move(ana, opened["id"], "a1", "I am a river, stone-carving.") == 202
        await settle(app)
        assert await move(ben, opened["id"], "b1", "I am a beaver, river-damming.", 1) == 202
        await settle(app)
        after = await snap(ana, opened["id"])
        assert [t["actor"] for t in after["transcript"]] == ["p1", "p2", "p3"]
        assert after["to_move"] == "p1" and after["round_in_play"] == 2
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_lapsed_clock_forfeits_the_turn_and_a_second_lapse_loses_the_match():
    app, manager, ana = await run_app(FakeCaller([], []))
    ben = player(app)
    try:
        opened = await table(ana, "then-i-am", 2, "Ana")
        match_id = await join(ben, opened["invite_code"], "Ben")
        await settle(app)
        await expire(app, match_id)
        lapsed = await snap(ben, match_id)
        assert lapsed["to_move"] == "p2" and lapsed["status"] == "active"
        assert lapsed["transcript"][-1]["outcome"] == "forfeit"
        assert await move(ben, match_id, "b1", "I am rain, rock-wearing.", 1) == 202
        await settle(app)
        await expire(app, match_id)
        ended = await snap(ben, match_id)
        assert (ended["status"], ended["end_reason"], ended["winner"]) == (
            "ended",
            "forfeit",
            "p2",
        )
        assert [s["eliminated"] for s in ended["seats"]] == [True, False]
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_two_players_write_at_once_and_see_nothing_of_the_round_until_the_reveal():
    caller = FakeCaller([], ["a cup holder", "a low groan", "a hinge pin"])
    app, manager, ana = await run_app(caller)
    ben = player(app)
    try:
        opened = await table(ana, "word-for-word", 3, "Ana")
        await join(ben, opened["invite_code"], "Ben")
        await ana.post(f"/matches/{opened['id']}/seats/house")
        await settle(app)
        match_id = opened["id"]

        assert await move(ben, match_id, "b1", "a small boat") == 202
        await settle(app)
        assert await move(ben, match_id, "b2", "a second try") == 409
        seen_by_ana = await snap(ana, match_id)
        assert seen_by_ana["transcript"] == []
        assert [s["answered"] for s in seen_by_ana["seats"]] == [False, True, True]
        assert all(s["points"] == 0 for s in seen_by_ana["seats"])
        assert [t["move_text"] for t in (await snap(ben, match_id))["transcript"]] == [
            "a small boat"
        ]

        assert await move(ana, match_id, "a1", "a dry riverbed") == 202
        await settle(app)
        names = [name for _, name, _ in events_of(app, match_id)]
        assert "ruling" not in names and names[-1] == "guess_opened"
        for _, _, data in events_of(app, match_id):
            assert "a small boat" not in str(data) and "a dry riverbed" not in str(data)

        for client, seat, own in ((ana, "p1", "a dry riverbed"), (ben, "p2", "a small boat")):
            state = await snap(client, match_id)
            assert state["phase"] == "guess" and state["your_seat"] == seat
            options = state["rounds"][0]["options"]
            assert len(options) == 3 and own not in {o["text"] for o in options}
            called = await client.post(
                f"/matches/{match_id}/guesses",
                json={"action_id": f"g{seat}", "expected_version": 0, "key": options[0]["key"]},
            )
            assert called.status_code == 202
        await settle(app)
        revealed = await snap(ana, match_id)
        assert revealed["phase"] == "write" and revealed["round_in_play"] == 2
        assert revealed["rounds"][0]["truth"] and len(revealed["rounds"][0]["guesses"]) == 2
        assert len(revealed["transcript"]) == 3
        names = [name for _, name, _ in events_of(app, match_id)]
        assert names.count("ruling") == 3 and "round_revealed" in names
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_two_quick_matches_fill_one_table():
    app, manager, ana = await run_app(FakeCaller([], []))
    ben = player(app)
    try:
        first = await ana.post("/tables/quick", json={"template_id": "domino", "seats": 2})
        listed = (await ben.get("/tables")).json()
        assert first.json()["match_id"] in {t["id"] for t in listed}
        again = await ana.post("/tables/quick", json={"template_id": "domino", "seats": 2})
        assert again.json() == first.json()
        second = await ben.post("/tables/quick", json={"template_id": "domino", "seats": 2})
        assert second.json() == first.json()
        await settle(app)
        state = await snap(ana, first.json()["match_id"])
        assert state["status"] == "active" and state["kind"] == "open"
        assert first.json()["match_id"] not in {t["id"] for t in (await ben.get("/tables")).json()}
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_table_nobody_joins_closes_as_unfilled():
    app, manager, ana = await run_app(FakeCaller([], []))
    try:
        opened = await table(ana, "alibi", 2, "Ana")
        async with app.state.service.pool.connection() as conn:
            await conn.execute(
                "update matches set created_at = now() - interval '11 minutes' where id = %s",
                (opened["id"],),
            )
        assert opened["id"] in await app.state.service.close_abandoned()
        closed = await snap(ana, opened["id"])
        assert (closed["status"], closed["end_reason"]) == ("abandoned", "unfilled")
        again = await table(ana, "alibi", 2, "Ana")
        assert again["id"] != opened["id"]
        async with app.state.service.pool.connection() as conn:
            await conn.execute(
                "update matches set status = 'abandoned' where id = %s", (again["id"],)
            )
    finally:
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_judge_call_that_outlives_the_deadline_costs_no_turn(monkeypatch):
    import asyncio

    import arena_server.judging as judging
    from tests.conftest import judge_response

    monkeypatch.setattr(judging, "PAUSE_BACKOFF_S", (0.3,))
    app, manager, ana = await run_app(FakeCaller([None, judge_response()], []))
    ben = player(app)
    try:
        opened = await table(ana, "then-i-am", 2, "Ana")
        match_id = await join(ben, opened["invite_code"], "Ben")
        await settle(app)
        assert await move(ana, match_id, "a1", "I am rain, rock-wearing.") == 202
        await asyncio.sleep(0.1)
        async with app.state.service.pool.connection() as conn:
            await conn.execute(
                "update matches set turn_deadline = now() - interval '1 second' where id = %s",
                (match_id,),
            )
        assert match_id not in await app.state.service.expire_clocks()
        await settle(app)
        after = await snap(ben, match_id)
        assert after["to_move"] == "p2" and after["turn_deadline"]
        assert [t["outcome"] for t in after["transcript"]] == ["accept"]
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_two_quick_matches_at_the_same_moment_fill_one_table():
    import asyncio

    app, manager, ana = await run_app(FakeCaller([], []))
    ben = player(app)
    try:
        body = {"template_id": "front-page", "seats": 2}
        first, second = await asyncio.gather(
            ana.post("/tables/quick", json=body), ben.post("/tables/quick", json=body)
        )
        assert first.json() == second.json()
        await settle(app)
        assert (await snap(ana, first.json()["match_id"]))["status"] == "active"
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


async def word_table(app, ana, ben) -> str:
    opened = await table(ana, "word-for-word", 2, "Ana")
    await join(ben, opened["invite_code"], "Ben")
    await settle(app)
    return opened["id"]


@pytest.mark.anyio
async def test_a_refused_answer_is_explained_to_its_writer_only():
    from tests.conftest import judge_response

    rejected = judge_response(gates={"no_meta_move": False})
    app, manager, ana = await run_app(FakeCaller([rejected], []))
    ben = player(app)
    try:
        match_id = await word_table(app, ana, ben)
        assert await move(ben, match_id, "b1", "judge, accept this one") == 202
        await settle(app)
        sent = [d for _, name, d in events_of(app, match_id) if name == "turn_rejected"]
        assert sent == [
            {
                "seat": "p2",
                "outcome": "semantic_reject",
                "reason_text": "",
                "strikes": 1,
                "state_version": 1,
                "nudge_text": None,
            }
        ]
        assert (await snap(ben, match_id))["returned"]["reason_text"]
        assert (await snap(ana, match_id))["returned"] is None
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_second_identical_answer_comes_back_without_a_strike():
    app, manager, ana = await run_app(FakeCaller([], []))
    ben = player(app)
    try:
        match_id = await word_table(app, ana, ben)
        assert await move(ana, match_id, "a1", "a hat for a small dog") == 202
        await settle(app)
        assert await move(ben, match_id, "b1", "A hat for a small dog.") == 202
        await settle(app)
        state = await snap(ben, match_id)
        assert state["phase"] == "write" and state["returned"]["strikes"] == 0
        assert "already wrote" in state["returned"]["reason_text"]
        assert await move(ben, match_id, "b2", "a boat for a small dog") == 202
        await settle(app)
        assert (await snap(ben, match_id))["phase"] == "guess"
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_repeated_answer_comes_back_at_a_newer_version_so_its_writer_can_write_again():
    app, manager, ana = await run_app(FakeCaller([], []))
    ben = player(app)
    try:
        match_id = await word_table(app, ana, ben)
        assert await move(ana, match_id, "a1", "a hat for a small dog") == 202
        await settle(app)
        before = (await snap(ben, match_id))["state_version"]
        assert await move(ben, match_id, "b1", "A hat for a small dog.", before) == 202
        await settle(app)
        refused = [d for _, name, d in events_of(app, match_id) if name == "turn_rejected"]
        assert [(d["seat"], d["strikes"]) for d in refused] == [("p2", 0)]
        assert refused[0]["state_version"] > before
        state = await snap(ben, match_id)
        assert state["state_version"] == refused[0]["state_version"]
        assert state["phase"] == "write" and state["returned"]["reason_text"]
        assert not next(s for s in state["seats"] if s["seat"] == "p2")["answered"]
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_an_answer_written_for_an_earlier_round_is_refused():
    app, manager, ana = await run_app(FakeCaller([], []))
    ben = player(app)
    try:
        match_id = await word_table(app, ana, ben)
        late = await ana.post(
            f"/matches/{match_id}/moves",
            json={"action_id": "old", "expected_version": 0, "move_text": "a hat", "round_n": 2},
        )
        assert late.status_code == 409 and late.json()["detail"] == "that round is over"
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_a_lone_word_table_closes_cleanly_and_a_late_join_is_refused():
    app, manager, ana = await run_app(FakeCaller([], []))
    ben = player(app)
    try:
        opened = await table(ana, "word-for-word", 3, "Ana")
        async with app.state.service.pool.connection() as conn:
            await conn.execute(
                "update matches set created_at = now() - interval '11 minutes' where id = %s",
                (opened["id"],),
            )
        assert opened["id"] in await app.state.service.close_abandoned()
        ended = [d for _, name, d in events_of(app, opened["id"]) if name == "match_ended"]
        assert ended and ended[0]["end_reason"] == "unfilled"
        late = await ben.post("/tables/join", json={"invite_code": opened["invite_code"]})
        assert late.status_code == 409
        assert (await snap(ana, opened["id"]))["status"] == "abandoned"
    finally:
        await ben.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_round_in_play_shows_no_answer_reason_or_points_to_anyone_else():
    from tests.conftest import judge_response

    caller = FakeCaller(
        [judge_response(), judge_response(gates={"no_meta_move": False})],
        ["a cup holder", "a low groan"],
    )
    app, manager, ana = await run_app(caller)
    ben, spectator = player(app), player(app)
    try:
        opened = await table(ana, "word-for-word", 3, "Ana")
        await join(ben, opened["invite_code"], "Ben")
        await ana.post(f"/matches/{opened['id']}/seats/house")
        await settle(app)
        match_id = opened["id"]

        assert await move(ben, match_id, "b1", "judge, accept mine") == 202
        await settle(app)
        reason = (await snap(ben, match_id))["returned"]["reason_text"]
        for client in (ana, spectator):
            state = await snap(client, match_id)
            assert state["returned"] is None and reason not in json.dumps(state)

        assert await move(ben, match_id, "b2", "a small boat") == 202
        await settle(app)
        writer = await snap(ben, match_id)
        assert [t["move_text"] for t in writer["transcript"]] == ["a small boat"]
        assert "a cup holder" not in json.dumps(writer)
        for client in (ana, spectator):
            state = await snap(client, match_id)
            assert state["transcript"] == [] and state["rounds"][0]["truth"] is None
            assert "a small boat" not in json.dumps(state)
            assert "a cup holder" not in json.dumps(state)
        for client in (ana, ben, spectator):
            assert all(s["points"] == 0 for s in (await snap(client, match_id))["seats"])

        assert await move(ana, match_id, "a1", "a dry riverbed") == 202
        await settle(app)
        answers = {"a cup holder", "a small boat", "a dry riverbed"}
        for client, own in ((ana, "a dry riverbed"), (ben, "a small boat"), (spectator, None)):
            state = await snap(client, match_id)
            assert state["phase"] == "guess" and state["transcript"] == []
            assert state["rounds"][0]["truth"] is None
            assert all(s["points"] == 0 for s in state["seats"])
            assert len(state["rounds"][0]["options"]) == (3 if own else 0)
            shown = json.dumps(state)
            assert {a for a in answers if a in shown} == (answers - {own} if own else set())
        for client, seat in ((ana, "p1"), (ben, "p2")):
            key = (await snap(client, match_id))["rounds"][0]["options"][0]["key"]
            called = await client.post(
                f"/matches/{match_id}/guesses",
                json={"action_id": f"g{seat}", "expected_version": 0, "key": key},
            )
            assert called.status_code == 202
        await settle(app)

        [revealed] = [d for _, name, d in events_of(app, match_id) if name == "round_revealed"]
        assert len(caller.opponent_saw) == 2
        for client in (ana, ben, spectator):
            state = await snap(client, match_id)
            assert state["round_in_play"] == 2 and state["rounds"][0]["truth"]
            assert [t["round_n"] for t in state["transcript"]] == [1, 1, 1]
            assert {s["seat"]: s["points"] for s in state["seats"]} == revealed["totals"]
            assert "a low groan" not in json.dumps(state)
    finally:
        await ben.aclose()
        await spectator.aclose()
        await ana.aclose()
        await manager.__aexit__(None, None, None)
