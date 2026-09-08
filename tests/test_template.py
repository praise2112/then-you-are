import pytest
from pydantic import ValidationError

from arena_core.template import Template, load_template


def test_template_loads_and_lints():
    template = load_template("then-i-am")
    assert template.slug == "then-i-am"
    assert template.weights == {"counter_strength": 5, "coherence": 3, "novelty": 2}
    assert len(template.seed_pool) >= 15


def test_player_projection_hides_examples_and_shows_points_available():
    projection = load_template("then-i-am").player_projection()
    assert "examples" not in projection
    assert [entry["max_points"] for entry in projection["rubric"]] == [20, 12, 8]
    assert projection["max_chars"] == 200


def test_lint_rejects_a_judge_out_text_without_the_slot():
    data = load_template("then-i-am").model_dump()
    data["judge_out_text"] = "The judge is out."
    with pytest.raises(ValidationError, match="standing_form"):
        Template.model_validate(data)
