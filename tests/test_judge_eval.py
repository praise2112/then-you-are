from arena_core.state import weighted_total
from arena_core.template import load_template
from arena_evals.judge_eval import RANK_GAP, EvalVerdict, kappa, score
from tests.conftest import judge_response

DUEL = load_template("then-i-am")
HIGH = {"counter_strength": 4, "coherence": 4, "novelty": 4}
LOW = {"counter_strength": 1, "coherence": 1, "novelty": 1}


def verdict(row: str, response) -> EvalVerdict:
    return EvalVerdict(
        id=f"c1/{row}",
        template_id="then-i-am",
        context="c1",
        row=row,
        messages=[],
        response=response.model_dump(),
    )


def test_an_unparseable_reply_disagrees_and_a_misordered_pair_fails_the_ranking():
    assert weighted_total(HIGH, DUEL.weights) - weighted_total(LOW, DUEL.weights) > RANK_GAP
    verdicts = [
        verdict("a", judge_response(scores=HIGH)),
        verdict("b", judge_response(scores=LOW)),
        verdict("c", judge_response(verdict="fail")),
    ]
    answers = {
        "c1/a": judge_response(scores=LOW).model_dump_json(),
        "c1/b": judge_response(scores=HIGH).model_dump_json(),
        "c1/c": "no json here",
    }
    report = score(verdicts, answers, {"then-i-am": DUEL})
    assert report.agreement == 2 / 3 and report.unparsed == 1 / 3
    assert report.ranking_pairs == 1 and report.ranking_agreement == 0.0
    assert report.score_error == 3.0


def test_kappa_is_one_for_identical_labels_and_zero_for_chance_agreement():
    assert kappa([("accept", "accept"), ("fail", "fail")]) == 1.0
    chance = [("accept", "accept"), ("accept", "fail"), ("fail", "accept"), ("fail", "fail")]
    assert kappa(chance) == 0.0
