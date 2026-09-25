import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import arena_core.template as template_module
from arena_core.template import Template, load_template, load_templates
from arena_judge.prompt import render_opponent_messages


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


def test_demo_round_carries_both_moves_points_and_openings_from_the_seed_pool():
    template = load_template("then-i-am")
    demo = template.player_projection()["demo"]
    assert [[p["earned"] for p in m["points"]] for m in demo["moves"]] == [[15, 12, 0], [20, 12, 4]]
    assert all(template.seed_named(o["token"]) for o in demo["openings"])
    assert demo["openings"][0]["examples"]


def test_lint_rejects_demo_move_scores_off_the_rubric():
    data = load_template("then-i-am").model_dump()
    data["demo"]["moves"][0]["scores"] = {"counter_strength": 3}
    with pytest.raises(ValidationError, match="demo move scores"):
        Template.model_validate(data)


def test_lint_rejects_an_unscored_second_demo_move():
    data = load_template("then-i-am").model_dump()
    data["demo"]["moves"][1]["scores"] = None
    with pytest.raises(ValidationError, match="second demo move"):
        Template.model_validate(data)


def test_lint_rejects_a_demo_with_one_landing_opening():
    data = load_template("then-i-am").model_dump()
    data["demo"]["openings"] = data["demo"]["openings"][:1]
    with pytest.raises(ValidationError, match="openings"):
        Template.model_validate(data)


def test_sentence_games_hide_the_move_medallions():
    flags = {slug: t.player_projection()["medallions"] for slug, t in load_templates().items()}
    assert flags["then-i-am"] and not flags["domino"] and not flags["alibi"]


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


def test_showcase_projection_carries_the_call_and_then_i_am_has_none():
    template = load_template("word-for-word")
    assert template.player_projection()["guess"] == {
        "spot_points": 10,
        "fool_points": 10,
        "prompt": "One of these is the real entry. Call it.",
    }
    assert load_template("then-i-am").player_projection()["guess"] is None


def test_lint_rejects_a_call_without_a_hidden_truth_or_outside_a_showcase():
    data = load_template("word-for-word").model_dump()
    data["seed_pool"][0]["hidden"] = ""
    with pytest.raises(ValidationError, match="hidden truth"):
        Template.model_validate(data)
    data = load_template("then-i-am").model_dump()
    data["guess"] = {"spot_points": 10, "fool_points": 10, "prompt": "Call it."}
    with pytest.raises(ValidationError, match="showcase"):
        Template.model_validate(data)


def test_opponent_prompt_carries_premise_criterion_and_move_limits():
    duel = load_template("then-i-am")
    system = render_opponent_messages(duel, "a rock", [])[0]["content"]
    assert system.startswith(duel.premise.strip())
    assert duel.criterion.description.strip() in system
    assert duel.criterion.anti_metagaming_clause.strip() in system
    assert 'at most 200 characters and starts with "I am "' in system
    assert system.endswith(duel.opponent_prompt.strip())
    assert "{max_chars}" not in system

    words = load_template("word-for-word")
    system = render_opponent_messages(words, "zarf", [])[0]["content"]
    assert "at most 160 characters. Reply with the move only." in system
    assert "truth_proximity" not in system


def test_a_shipped_game_with_a_long_tagline_is_refused(monkeypatch, tmp_path: Path):
    src = template_module.TEMPLATES_DIR / "domino" / "v1.yaml"
    dst = tmp_path / "domino" / "v1.yaml"
    dst.parent.mkdir()
    shutil.copyfile(src, dst)
    dst.write_text(dst.read_text().replace("Make it worse, one step at a time.", "x" * 41))
    monkeypatch.setattr(template_module, "TEMPLATES_DIR", tmp_path)
    with pytest.raises(ValueError, match="tagline"):
        template_module.load_templates()


def test_a_showcase_opponent_sees_this_rounds_card_after_the_earlier_rounds():
    words = load_template("front-page")
    earlier = ["round 1, prompt: The council planted twelve trees along a road.", "player1: TREES"]
    user = render_opponent_messages(words, "A neighbour borrowed a ladder.", earlier)[1]["content"]
    assert user.index("twelve trees") < user.index("ladder") < user.index("YOUR MOVE")
    assert user.rstrip().endswith("THIS ROUND'S CARD: A neighbour borrowed a ladder.\n\nYOUR MOVE:")
