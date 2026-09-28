import asyncio
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from arena_core.template import load_template
from arena_evals.datagen.ledger import CallFailed, Ledger
from arena_evals.variants import funnel
from arena_evals.variants.funnel import (
    Calibration,
    Stats,
    agreement_interval,
    bootstrap,
    deal_for,
    match_stats,
    play_pilot,
    rejudge,
    sanity_reasons,
    stood_positions,
)
from arena_judge.caller import CallError, ModelSpec
from tests.conftest import judge_response
from tests.test_datagen_play import ScriptedCaller

DUEL = load_template("then-i-am")
WORDS = load_template("word-for-word")
MOVES = [
    "I am a hammer, rock-splitting.",
    "I am rust, hinge-eating, patient.",
    "I am a poem.",
    "I am the sea, salt-heavy.",
    "I am the sun, water-drinking.",
    "I am a cloud.",
]


def high() -> dict:
    return {"counter_strength": 4, "coherence": 4, "novelty": 3}


def low() -> dict:
    return {"counter_strength": 1, "coherence": 2, "novelty": 0}


def played(tmp_path: Path) -> tuple[Ledger, ScriptedCaller, list[str]]:
    tmp_path.mkdir(exist_ok=True)
    ledger = Ledger(tmp_path / "pilot.db")
    caller = ScriptedCaller(
        rulings=[
            judge_response(scores=high()),
            judge_response(scores=low()),
            judge_response(verdict="fail", scores=low()),
        ]
        * 2,
        moves=list(MOVES),
    )
    ended = asyncio.run(
        play_pilot(DUEL, "duel", 2, ledger, caller, budget=1.0, spent=lambda: 0.0)  # type: ignore[arg-type]
    )
    return ledger, caller, ended


def test_pilot_stats_come_from_the_judged_moves_and_land_inside_their_own_intervals(tmp_path):
    ledger, _, ended = played(tmp_path)
    assert ended == ["duel-0", "duel-1"]
    stats = match_stats(DUEL, ledger, ended)
    assert stats == Stats(
        matches=2,
        judged=6,
        refused=0,
        pass_rate=1.0,
        spread=stats.spread,
        dup_rate=0.0,
        median_length=3,
    )
    assert stats.spread > 0.1
    intervals = bootstrap(DUEL, ledger, ended, size=2, rng=random.Random(0))
    cal = Calibration.model_validate(
        {"matches": 2, "pilot_size": 2, "agreement_same": [0.9, 1.0], "agreement_luna": [0.8, 1.0]}
        | intervals
    )
    assert cal.pass_rate == [1.0, 1.0] and cal.median_length == [3.0, 3.0]
    assert sanity_reasons(stats, cal) == []
    weak = stats.model_copy(
        update={"pass_rate": 0.4, "dup_rate": cal.dup_rate[1] + 0.1, "median_length": 2.0}
    )
    reasons = sanity_reasons(weak, cal)
    assert [r.split()[0] for r in reasons] == ["pass", "duplicates", "median"]


def test_no_sanity_bar_is_stricter_than_what_the_parents_own_pilots_show():
    cal = Calibration(
        matches=100,
        pilot_size=10,
        pass_rate=[1.0, 1.0],
        spread=[0.1, 0.2],
        dup_rate=[0.0, 0.2],
        median_length=[1.0, 3.5],
        agreement_same=[0.9, 1.0],
        agreement_luna=[1.0, 1.0],
    )
    edge = Stats(
        matches=10,
        judged=30,
        refused=0,
        pass_rate=0.9,
        spread=0.1,
        dup_rate=0.2,
        median_length=1.0,
    )
    assert sanity_reasons(edge, cal) == []
    worse = edge.model_copy(update={"pass_rate": 0.5, "dup_rate": 0.25, "median_length": 0.5})
    assert [r.split()[0] for r in sanity_reasons(worse, cal)] == ["pass", "duplicates", "median"]


def test_pilot_cards_are_the_same_on_a_rerun_and_a_showcase_gets_one_per_round():
    assert deal_for(DUEL, "duel", 0) == deal_for(DUEL, "duel", 0)
    assert deal_for(DUEL, "duel", 0) != deal_for(DUEL, "duel", 1) or len(DUEL.seed_pool) == 1
    cards = deal_for(WORDS, "words", 3)
    assert len(cards) == WORDS.rounds_budget and len({c.opening_token for c in cards}) == 3


def test_rejudge_measures_agreement_and_never_asks_twice(tmp_path):
    ledger, caller, ended = played(tmp_path)
    stood = stood_positions(ledger, ended)
    assert len(stood) == 4 and {p.outcome for p in stood} == {"accept"}
    judged_before = len(caller.judged)
    spec = ModelSpec(model="again", display_name="Again")
    caller.rulings[:] = [judge_response(), judge_response(verdict="fail"), judge_response()]
    same = asyncio.run(rejudge(DUEL, ledger, "duel/rejudge", stood, spec, caller))
    assert same == [True, False, True, True]
    assert len(caller.judged) == judged_before + 4
    again = asyncio.run(rejudge(DUEL, ledger, "duel/rejudge", stood, spec, caller))
    assert again == same and len(caller.judged) == judged_before + 4
    shuffled = list(reversed(stood))
    assert asyncio.run(rejudge(DUEL, ledger, "duel/rejudge", shuffled, spec, caller)) == same[::-1]
    assert len(caller.judged) == judged_before + 4
    assert agreement_interval(same, random.Random(0))[1] == 1.0
    assert agreement_interval([], random.Random(0)) == [0.0, 0.0]


def test_a_spent_budget_plays_no_pilot_match_but_keeps_the_ones_already_ended(tmp_path):
    ledger = Ledger(tmp_path / "pilot.db")
    caller = ScriptedCaller(rulings=[], moves=list(MOVES))
    ended = asyncio.run(
        play_pilot(DUEL, "duel", 2, ledger, caller, budget=0.5, spent=lambda: 0.5)  # type: ignore[arg-type]
    )
    assert ended == [] and caller.completed == 0
    ledger, caller, ended = played(tmp_path / "second")
    assert ended == ["duel-0", "duel-1"]
    before = caller.completed
    again = asyncio.run(
        play_pilot(DUEL, "duel", 3, ledger, caller, budget=0.5, spent=lambda: 0.5)  # type: ignore[arg-type]
    )
    assert again == ["duel-0", "duel-1"] and caller.completed == before
    assert funnel.PILOT_MATCHES == 10


def test_moves_the_rule_check_refuses_count_against_the_pass_rate_and_the_stock_move_is_left_out(
    tmp_path,
):
    ledger = Ledger(tmp_path / "pilot.db")
    too_long = "I am " + "a" * (DUEL.move_constraints.max_chars + 1)
    caller = ScriptedCaller(
        rulings=[judge_response(verdict="fail")] * 2,
        moves=[too_long] * DUEL.strikes_before_consequence,
    )
    ended = asyncio.run(
        play_pilot(DUEL, "long", 1, ledger, caller, budget=1.0, spent=lambda: 0.0)  # type: ignore[arg-type]
    )
    stats = match_stats(DUEL, ledger, ended)
    assert stats.refused == DUEL.strikes_before_consequence and stats.judged == 0
    assert stats.pass_rate == 0 and stats.dup_rate == 0


def test_a_failed_rejudge_is_asked_again_and_never_counts_as_disagreement(tmp_path):
    ledger, caller, ended = played(tmp_path)
    stood = stood_positions(ledger, ended)[:1]
    spec = ModelSpec(model="again", display_name="Again")
    caller.rulings[:] = [None]
    with pytest.raises(CallFailed):
        asyncio.run(rejudge(DUEL, ledger, "duel/rejudge", stood, spec, caller))
    caller.rulings[:] = [judge_response()]
    assert asyncio.run(rejudge(DUEL, ledger, "duel/rejudge", stood, spec, caller)) == [True]


def test_a_call_that_fails_past_its_retries_skips_that_variant_and_a_402_stops_the_class(
    tmp_path, monkeypatch
):
    class Closing:
        async def aclose(self):
            pass

    async def plenty(_):
        return 10.0

    rank = SimpleNamespace(klass="counter", stage="rank", reject=None)
    monkeypatch.setattr(funnel, "load_index", lambda: {s: rank for s in ("a", "b", "c")})
    monkeypatch.setattr(funnel, "PILOTS_DIR", tmp_path)
    monkeypatch.setattr(funnel, "make_caller", lambda **_: Closing())
    monkeypatch.setattr(funnel, "credit_left", plenty)
    failures = {"a": CallError("throttled", 429), "b": CallError("no credit", 402)}
    reached = []

    async def step(slug, ledger, caller, left):
        reached.append(slug)
        if slug in failures:
            raise failures[slug]
        return 0.0

    asyncio.run(funnel.for_each("counter", "rank", 1.0, step))
    assert reached == ["a", "b"]
