import asyncio

import pytest

from arena_evals.grow_seeds import (
    RETRY_STATUSES,
    Candidate,
    cosine,
    head_noun,
    is_single_emoji,
    mechanical_reason,
    parse_batch,
    with_backoff,
)
from arena_judge.caller import CallError


def test_head_noun_singularises_and_reads_of_clauses():
    assert head_noun("a pebble") == "pebble"
    assert head_noun("some pebbles") == "pebble"
    assert head_noun("a house of cards") == "house"
    assert head_noun("a box of matches") == "box"
    assert head_noun("a squeaky hinge") == "hinge"


def test_single_emoji_accepts_variation_selectors_and_rejects_words():
    assert is_single_emoji("🕯️")
    assert is_single_emoji("⛄")
    assert is_single_emoji("👩\u200d🏫")
    assert not is_single_emoji("🔥🔥")
    assert not is_single_emoji("fire")
    assert not is_single_emoji("")


def test_mechanical_reason_names_the_first_failure():
    ok = Candidate(opening="a snowman", emoji="⛄", counter="the sun")
    assert mechanical_reason(ok) is None
    assert mechanical_reason(ok.model_copy(update={"opening": "snowman"})) == "no article"
    assert mechanical_reason(ok.model_copy(update={"opening": "a Snowman"})) == "capitalised"
    assert mechanical_reason(ok.model_copy(update={"opening": "a Monday morning"})) is None
    assert mechanical_reason(ok.model_copy(update={"counter": "sun"})) == "no counter"
    assert mechanical_reason(ok.model_copy(update={"opening": "a ant"})) == "wrong article"
    assert mechanical_reason(ok.model_copy(update={"opening": "an egg"})) is None
    assert mechanical_reason(ok.model_copy(update={"opening": "a dough rising"})) == "dangling verb"
    long = ok.model_copy(update={"opening": "a very old gate"})
    assert mechanical_reason(long) == "too long"


def test_parse_batch_finds_json_inside_prose():
    raw = (
        'Here you go:\n{"candidates": [{"opening": "a kite", "emoji": "🪁", "counter": "no wind"}]}'
    )
    batch = parse_batch(raw)
    assert batch is not None and batch.candidates[0].opening == "a kite"
    assert parse_batch("nothing here") is None


def test_cosine_is_one_for_identical_vectors():
    assert cosine([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_backoff_retries_rate_limits_and_gives_up_on_client_errors(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _: real_sleep(0))
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise CallError("slow down", 429)
        return "ok"

    assert asyncio.run(with_backoff(flaky)) == "ok"
    assert calls["n"] == 3
    assert 429 in RETRY_STATUSES

    async def bad_request():
        raise CallError("nope", 400)

    with pytest.raises(CallError):
        asyncio.run(with_backoff(bad_request))
