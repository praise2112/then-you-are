import pytest

from arena_core.template import load_template
from arena_evals.variants.spec import (
    check_against_examples,
    field_value,
    load_spec,
    spec_names,
)


def test_every_shipped_class_has_a_spec_that_matches_its_examples():
    assert spec_names() == ["build", "counter", "showcase"]
    covered = set()
    for name in spec_names():
        spec, templates = load_spec(name)
        assert spec.name == name
        covered.update(templates)
    assert covered == {"then-i-am", "word-for-word", "domino", "alibi", "front-page"}


def test_fixed_paths_read_the_mechanic_and_nothing_a_variant_rewrites():
    spec, templates = load_spec("counter")
    duel = templates["then-i-am"]
    assert field_value(duel, "criterion.verb") == "overcomes"
    assert field_value(duel, "guess") is None
    assert "premise" not in spec.fixed and "opponent_prompt" not in spec.fixed


def test_a_profile_that_does_not_fit_the_rubric_is_refused():
    spec, templates = load_spec("build")
    bad = spec.model_copy(deep=True)
    bad.axes.weights.append([5, 3])
    with pytest.raises(ValueError, match="does not fit"):
        check_against_examples(bad, templates)
    bad = spec.model_copy(deep=True)
    bad.axes.weights.append([2, 3, 5])
    with pytest.raises(ValueError, match="not descending"):
        check_against_examples(bad, templates)


def test_showcase_has_no_amplification_and_counter_fails_it():
    showcase, _ = load_spec("showcase")
    counter, _ = load_spec("counter")
    assert "amplification" not in showcase.sabotage_expectations
    assert counter.sabotage_expectations["amplification"] == "fail"
    words = load_template("word-for-word")
    with pytest.raises(ValueError, match="sabotage kinds"):
        check_against_examples(counter, {"word-for-word": words})
