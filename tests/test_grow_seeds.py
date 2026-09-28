import asyncio

import pytest

from arena_core.template import TEMPLATES_DIR, Seed, load_template, load_template_file
from arena_evals import grow_seeds
from arena_evals.common import RETRY_STATUSES, with_backoff
from arena_evals.grow_seeds import (
    Candidate,
    Shape,
    append_to_pool,
    card_shape,
    cosine,
    head_noun,
    is_single_emoji,
    mechanical_reason,
    parse_batch,
    seed_line,
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


SHORT = Shape(short_form=True, max_words=3, needs_truth=False)


def test_mechanical_reason_names_the_first_failure():
    ok = Candidate(opening="a snowman", emoji="⛄", answer="the sun")
    assert mechanical_reason(ok, SHORT) is None
    assert mechanical_reason(ok.model_copy(update={"opening": "snowman"}), SHORT) == "no article"
    assert mechanical_reason(ok.model_copy(update={"opening": "a Snowman"}), SHORT) == "capitalised"
    assert mechanical_reason(ok.model_copy(update={"opening": "a Monday morning"}), SHORT) is None
    assert mechanical_reason(ok.model_copy(update={"answer": "sun"}), SHORT) == "no answer"
    assert mechanical_reason(ok.model_copy(update={"opening": "a ant"}), SHORT) == "wrong article"
    assert mechanical_reason(ok.model_copy(update={"opening": "an egg"}), SHORT) is None
    dangling = ok.model_copy(update={"opening": "a dough rising"})
    assert mechanical_reason(dangling, SHORT) == "dangling verb"
    long = ok.model_copy(update={"opening": "a very old gate in the wall"})
    assert mechanical_reason(long, SHORT) == "too long"


def test_a_game_whose_own_cards_break_the_short_form_style_is_not_held_to_it():
    then = load_template("then-i-am")
    own = [
        c.model_copy(update={"opening_token": w})
        for c, w in zip(
            then.seed_pool[:3], ["Potato, baked.", "Rice, boiled.", "Carrot, roasted."], strict=True
        )
    ]
    shape = card_shape(then.model_copy(update={"seed_pool": own}))
    assert not shape.short_form
    card = Candidate(opening="Leek, braised.", emoji="🥬", answer="a soup pot")
    assert mechanical_reason(card, shape) is None


def test_card_shape_comes_from_the_pool_and_sentences_skip_the_short_form_checks():
    assert card_shape(load_template("then-i-am")) == Shape(True, 3, False)
    domino = card_shape(load_template("domino"))
    assert not domino.short_form and domino.max_words >= 12
    sentence = Candidate(
        opening="The office printer jams five minutes before the big meeting.",
        emoji="🖨️",
        answer="the boss walks in",
    )
    assert mechanical_reason(sentence, domino) is None
    assert (
        mechanical_reason(sentence.model_copy(update={"opening": " ".join(["a"] * 40)}), domino)
        == "too long"
    )
    words = Shape(short_form=False, max_words=2, needs_truth=True)
    bare = Candidate(opening="zarf", emoji="☕", answer="a cup holder")
    assert mechanical_reason(bare, words) == "no truth"
    assert (
        mechanical_reason(bare.model_copy(update={"detail": "noun", "hidden": "a holder"}), words)
        is None
    )


def test_append_to_pool_extends_any_template_file_layout(tmp_path):
    for slug in ("word-for-word", "front-page"):
        template = load_template(slug)
        path = tmp_path / f"{slug}.yaml"
        path.write_text((load_template.__globals__["TEMPLATES_DIR"] / slug / "v1.yaml").read_text())
        seed = Seed(opening_token="a fresh card", opening_emoji="🃏", detail="d", hidden="h")
        append_to_pool(template, [seed], path)
        grown = load_template_file(path)
        assert grown.seed_pool[-1] == seed and len(grown.seed_pool) == len(template.seed_pool) + 1
    assert seed_line(Seed(opening_token='say "hi"', opening_emoji="👋")) == (
        '  - { opening_token: "say \\"hi\\"", opening_emoji: "👋" }\n'
    )


def test_parse_batch_finds_json_inside_prose():
    raw = (
        'Here you go:\n{"candidates": [{"opening": "a kite", "emoji": "🪁", "answer": "no wind"}]}'
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


def test_growing_carries_on_past_an_empty_round_and_stalls_after_three(monkeypatch):
    class Idle:
        def __init__(self, **refs):
            pass

        async def aclose(self):
            pass

    first = load_template("then-i-am").seed_pool[0]

    def fresh(n: int) -> list[Seed]:
        return [first.model_copy(update={"opening_token": f"a fresh card {n}"})]

    async def probe(self, cell, avoid):
        return [Candidate(opening="a kite", emoji="🪁", answer="a gust of wind")]

    async def nothing(self):
        pass

    def rounds(script):
        batches = iter(script)

        async def round_(self, cells, rejects):
            return next(batches)

        return round_

    monkeypatch.setattr(grow_seeds, "make_caller", Idle)
    monkeypatch.setattr(grow_seeds.Grower, "generate_cell", probe)
    monkeypatch.setattr(grow_seeds.Grower, "load_pool_vectors", nothing)
    path = TEMPLATES_DIR / "then-i-am" / "v1.yaml"

    monkeypatch.setattr(grow_seeds.Grower, "round", rounds([[], fresh(1), [], fresh(2)]))
    report = asyncio.run(grow_seeds.grow(path, 2, 1, 0.65, dry_run=True))
    assert [s.opening_token for s in report.accepted] == ["a fresh card 1", "a fresh card 2"]
    assert not report.stalled

    monkeypatch.setattr(grow_seeds.Grower, "round", rounds([[], [], [], fresh(3)]))
    report = asyncio.run(grow_seeds.grow(path, 1, 1, 0.65, dry_run=True))
    assert report.accepted == [] and report.stalled

    monkeypatch.setattr(grow_seeds.Grower, "round", rounds([fresh(4), fresh(5)]))
    report = asyncio.run(grow_seeds.grow(path, 2, 1, 0.65, dry_run=True, budget=0.0))
    assert len(report.accepted) == 1 and not report.stalled


def test_a_game_whose_cards_share_one_template_is_not_held_to_distinct_head_nouns():
    then = load_template("then-i-am")
    assert card_shape(then).distinct_heads
    budget = [
        "an opening limit of £100 with a £3 comic",
        "an opening limit of £150 with a £5 sandwich",
    ]
    own = [
        c.model_copy(update={"opening_token": w})
        for c, w in zip(then.seed_pool[:2], budget, strict=True)
    ]
    assert not card_shape(then.model_copy(update={"seed_pool": own})).distinct_heads
