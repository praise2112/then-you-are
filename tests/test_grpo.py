import asyncio

from arena_core.state import weighted_total
from arena_core.template import load_template
from arena_evals.datagen.ledger import Ledger
from arena_evals.datagen.prefset import Position
from arena_judge.caller import ModelSpec
from arena_judge.schema import SCORE_MAX
from arena_train.grpo import Spot, informative, judge_move, reward_of
from tests.conftest import FakeCaller, judge_response

DUEL = load_template("then-i-am")
SPEC = ModelSpec(model="flash", display_name="flash")


def spot() -> Spot:
    position = Position(
        id="m/3",
        template_id="then-i-am",
        player_messages=[{"role": "user", "content": "a rock"}],
        judge_messages=[],
        teacher_move="I am a hammer",
        on_table=["a rock", "I am rust"],
    )
    inputs = {"previous": "a rock", "hidden": "", "transcript": ["a rock"]}
    return Spot(position, DUEL, "p1", 3, inputs)


def judge(move: str, caller: FakeCaller, ledger: Ledger):
    return asyncio.run(judge_move(spot(), move, ledger, caller, SPEC, lambda: False))


def test_a_refused_move_earns_nothing_and_never_reaches_the_judge(tmp_path):
    caller = FakeCaller([], [])
    with_repeat = judge("I am rust", caller, Ledger(tmp_path / "rl.db"))
    too_long = judge("I am " + "very " * 200, caller, Ledger(tmp_path / "rl.db"))
    assert {with_repeat.outcome, too_long.outcome} == {"refused"} and caller.judged == []
    assert reward_of(with_repeat, DUEL) == 0.0


def test_a_standing_move_outscores_a_failed_one_with_the_same_scores(tmp_path):
    response = judge_response()
    total = weighted_total(response.scoring.scores, DUEL.weights)
    quality = 0.5 * total / (SCORE_MAX * sum(DUEL.weights.values()))
    stood = judge("I am a hammer", FakeCaller([response], []), Ledger(tmp_path / "a.db"))
    failed = judge(
        "I am a hammer", FakeCaller([judge_response("fail")], []), Ledger(tmp_path / "b.db")
    )
    assert reward_of(stood, DUEL) == 1.0 + quality
    assert reward_of(failed, DUEL) == quality


def test_a_move_judged_before_at_the_same_position_replays_without_a_call(tmp_path):
    ledger = Ledger(tmp_path / "rl.db")
    caller = FakeCaller([judge_response(), judge_response("fail")], [])
    first = judge("I am a hammer", caller, ledger)
    again = judge("I am a hammer", caller, ledger)
    assert first == again and caller.judged == ["I am a hammer"]


def test_training_keeps_positions_whose_earlier_draws_disagreed():
    rows = [
        {"position": "mixed", "totals": [30], "passes": True},
        {"position": "mixed", "totals": [10], "passes": False},
        {"position": "spread", "totals": [34], "passes": True},
        {"position": "spread", "totals": [20], "passes": True},
        {"position": "easy", "totals": [33], "passes": True},
        {"position": "easy", "totals": [32], "passes": True},
        {"position": "hopeless", "totals": [5], "passes": False},
    ]
    assert informative(rows) == {"mixed", "spread"}
