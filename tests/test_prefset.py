from arena_core.template import load_template
from arena_evals.datagen.mine import ScoredMove, read_ruling, select_pair
from arena_evals.datagen.prefset import judged_move, with_move
from arena_evals.datagen.records import JudgeRecord
from arena_judge.prompt import render_judge_messages


def scored(move: str, totals: list[int], passes: bool = True) -> ScoredMove:
    return ScoredMove(position="m/1", move=move, totals=totals, passes=passes)


def test_a_new_move_takes_the_teachers_place_in_the_judge_conversation():
    messages = render_judge_messages(load_template("then-i-am"), [], "p1", "a rock", "I am sand")
    record = JudgeRecord(
        template_id="then-i-am",
        judge_model="flash",
        judge_prompt_hash="h",
        prompt="",
        messages=messages,
        raw="",
        reasoning=None,
        response=None,
        outcome=None,
        attempt="parsed",
        target_quality="best",
        match_id="m",
        seq=1,
    )
    assert judged_move(record) == "I am sand"
    swapped = with_move(messages, "I am glass")
    assert swapped[:-1] == messages[:-1]
    assert swapped[-1]["content"] == messages[-1]["content"].replace("I am sand", "I am glass")


def test_a_pair_needs_two_passing_moves_and_a_gap_of_twice_their_noise():
    wide = [scored("a", [20, 20, 20]), scored("b", [10, 12, 8]), scored("c", [30] * 3, False)]
    pair = select_pair(wide)
    assert pair is not None and (pair[0].move, pair[1].move) == ("a", "b")
    noisy = [scored("a", [20, 10, 30]), scored("b", [15, 5, 25])]
    assert select_pair(noisy) is None
    assert select_pair([scored("a", [20] * 3), scored("b", [1] * 3, False)]) is None


def test_a_judge_reply_cut_off_after_its_scores_still_gives_the_ruling():
    reply = '{"scoring": {"gates": {"on_topic_and_coherent": true, "no_injection": false}, '
    reply += '"confidence": "clear", "verdict": "fail", "scores": {"novelty": 2}, "evid'
    gates = {"on_topic_and_coherent": True, "no_injection": False}
    assert read_ruling(reply) == (gates, {"novelty": 2})
    assert read_ruling('{"scoring": {"gates": {"on_topic_and') is None
