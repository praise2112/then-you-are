import asyncio
import random
from pathlib import Path

from arena_core.template import load_template
from arena_evals.datagen.ledger import Ledger
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
from arena_judge.caller import ModelSpec
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
        matches=2, judged=6, pass_rate=1.0, spread=stats.spread, dup_rate=0.0, median_length=3
    )
    assert stats.spread > 0.1
    intervals = bootstrap(DUEL, ledger, ended, size=2, rng=random.Random(0))
    cal = Calibration.model_validate(
        {"matches": 2, "pilot_size": 2, "agreement_same": [0.9, 1.0], "agreement_luna": [0.8, 1.0]}
        | intervals
    )
    assert cal.pass_rate == [1.0, 1.0] and cal.median_length == [3.0, 3.0]
    assert sanity_reasons(stats, cal) == []
    weak = stats.model_copy(update={"pass_rate": 0.4, "dup_rate": 0.3, "median_length": 2.0})
    reasons = sanity_reasons(weak, cal)
    assert [r.split()[0] for r in reasons] == ["pass", "duplicates", "median"]


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
