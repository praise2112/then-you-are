import pytest
from pydantic import ValidationError

from arena_core.template import Template, load_template, load_templates


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


def test_demo_round_carries_points_and_openings_from_the_seed_pool():
    template = load_template("then-i-am")
    demo = template.player_projection()["demo"]
    assert [p["earned"] for p in demo["points"]] == [20, 12, 4]
    assert all(template.seed_named(o["token"]) for o in demo["openings"])
    assert demo["openings"][0]["examples"]


def test_lint_rejects_a_demo_opening_outside_the_seed_pool():
    data = load_template("then-i-am").model_dump()
    data["demo"]["openings"][0]["token"] = "a unicorn"
    with pytest.raises(ValidationError, match="seed pool"):
        Template.model_validate(data)


def test_every_template_on_disk_loads():
    assert set(load_templates()) >= {"then-i-am", "word-for-word"}


def test_showcase_projection_carries_rounds_and_the_demo_reveal():
    template = load_template("word-for-word")
    projection = template.player_projection()
    assert projection["mode"] == "showcase" and projection["rounds"] == 3
    assert projection["demo"]["opening"]["detail"].startswith("noun")
    assert projection["demo"]["opening"]["reveal"]
    assert load_template("then-i-am").player_projection()["rounds"] is None


def test_lint_rejects_a_showcase_decided_by_sudden_death():
    data = load_template("word-for-word").model_dump()
    data["win_condition"] = "sudden_death"
    with pytest.raises(ValidationError, match="points_total"):
        Template.model_validate(data)


def test_lint_rejects_an_odd_showcase_budget():
    data = load_template("word-for-word").model_dump()
    data["move_budget"] = 5
    with pytest.raises(ValidationError, match="even"):
        Template.model_validate(data)


def test_card_text_pairs_the_word_with_its_detail():
    card = load_template("word-for-word").seed_named("zarf")
    assert card and card.card_text == "zarf (noun, from Arabic via Turkish)"
    assert load_template("then-i-am").seed_pool[0].card_text == "a balloon"
