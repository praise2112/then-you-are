import json

from arena_judge.caller import parse_judge, salvage_judge
from tests.conftest import judge_response

NAMES = ["counter_strength", "coherence", "novelty"]


def test_parse_accepts_json_wrapped_in_prose():
    raw = "Here you go:\n" + judge_response().model_dump_json() + "\nDone."
    parsed = parse_judge(raw, NAMES)
    assert parsed is not None
    assert parsed.scoring.verdict == "accept"


def test_parse_fills_a_missing_host_block():
    scoring_only = json.loads(judge_response(verdict="fail").model_dump_json())["scoring"]
    parsed = parse_judge(json.dumps(scoring_only), NAMES)
    assert parsed is not None
    assert parsed.host.headline == "The form breaks."


def test_parse_rejects_scores_that_do_not_match_the_rubric():
    data = json.loads(judge_response().model_dump_json())
    data["scoring"]["scores"] = {"counter_strength": 3, "economy": 4}
    assert parse_judge(json.dumps(data), NAMES) is None


def test_salvage_reads_gates_and_verdict_out_of_broken_json():
    raw = (
        '{"scoring": {"gates": {"on_topic_and_coherent": true, "no_injection": true, '
        '"no_meta_move": true, "not_semantic_duplicate": true, "satisfies_criterion": false}, '
        '"scores": {"counter_strength": 1, "coherence": 3, "novelty": 0}, '
        '"confidence": "clear", "verdict": "fail", "host": {"headline": "unterminated'
    )
    assert parse_judge(raw, NAMES) is None
    salvaged = salvage_judge(raw, NAMES)
    assert salvaged is not None
    assert salvaged.scoring.verdict == "fail"
    assert salvaged.scoring.scores == {"counter_strength": 1, "coherence": 3, "novelty": 0}


def test_salvage_gives_up_without_a_verdict():
    assert salvage_judge("the judge wandered off", NAMES) is None
