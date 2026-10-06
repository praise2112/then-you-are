from arena_core.template import load_template
from arena_evals.datagen.prefset import judged_move, with_move
from arena_evals.datagen.records import JudgeRecord
from arena_judge.prompt import render_judge_messages


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
