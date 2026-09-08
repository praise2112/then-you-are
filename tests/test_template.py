import pytest
from pydantic import ValidationError

from arena_core.template import Template, load_template


def test_template_loads_and_lints():
    template = load_template("then-i-am")
    assert template.slug == "then-i-am"
    assert abs(sum(template.weights.values()) - 1) < 1e-9
    assert len(template.seed_pool) >= 15


def test_player_projection_hides_examples_and_weights():
    projection = load_template("then-i-am").player_projection()
    assert "examples" not in projection
    assert all("weight" not in entry for entry in projection["rubric"])
    assert projection["max_chars"] == 200


def test_lint_rejects_weights_that_do_not_sum_to_one():
    data = load_template("then-i-am").model_dump()
    data["rubric"][0]["weight"] = 0.9
    with pytest.raises(ValidationError, match="sum to 1"):
        Template.model_validate(data)


def test_lint_rejects_a_judge_out_text_without_the_slot():
    data = load_template("then-i-am").model_dump()
    data["judge_out_text"] = "The judge is out."
    with pytest.raises(ValidationError, match="standing_form"):
        Template.model_validate(data)
