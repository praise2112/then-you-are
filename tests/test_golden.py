from arena_core.template import load_template
from arena_evals.run_golden import headline_faults, load_golden
from arena_judge.prompt import render_judge_prompt


def test_both_splits_load_for_every_template():
    for template_id in ("then-i-am", "word-for-word"):
        for split in ("dev", "holdout"):
            records = load_golden(template_id, split)
            assert records
            assert {r.template_id for r in records} == {template_id}


def test_showcase_golden_records_carry_the_truth():
    records = load_golden("word-for-word", "dev")
    assert all(r.hidden for r in records)
    assert all("(" in r.previous_move for r in records)


def test_headline_checker_flags_status_openers_and_dashes():
    assert headline_faults("Accepted: the pebble has nowhere to hide.") == ["status opener"]
    assert headline_faults("The Ringmaster refuses: grit is not victory.") == ["status opener"]
    assert headline_faults("The goat advances — by refusing to scratch.") == ["dash"]
    assert headline_faults("The balloon had plans; the wind has custody.") == []


def test_good_headlines_pass_the_checker_and_reach_the_prompt():
    template = load_template("then-i-am")
    assert all(headline_faults(line) == [] for line in template.host.good_headlines)
    prompt = render_judge_prompt(template, [], "a rose", "I am frost, petal-blackening, overnight.")
    assert template.host.good_headlines[0] in prompt
    assert template.host.bad_headlines[0].text in prompt


def test_headline_checker_allows_a_status_word_used_as_a_word():
    assert headline_faults("Accepted is a verdict, not a form. Bring another form.") == []


def test_headline_checker_knows_each_host_by_name():
    assert headline_faults("The Lexicographer refuses: define it.", "The Lexicographer") == [
        "status opener"
    ]
    assert headline_faults("The Lexicographer refuses: define it.") == []
