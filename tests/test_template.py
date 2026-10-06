import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import arena_core.template as template_module
from arena_core.template import Template, load_template, load_templates
from arena_judge.prompt import JudgedTurn, render_judge_messages, render_opponent_messages
from arena_server.views import template_view


def test_template_loads_and_lints():
    template = load_template("then-i-am")
    assert template.slug == "then-i-am"
    assert template.weights == {"counter_strength": 5, "coherence": 3, "novelty": 2}
    assert len(template.seed_pool) >= 15


def test_template_view_hides_examples_and_shows_points_available():
    view = template_view(load_template("then-i-am"), featured=False)
    assert "examples" not in view.model_dump()
    assert [entry.max_points for entry in view.rubric] == [20, 12, 8]
    assert view.max_chars == 200


def test_lint_rejects_a_judge_out_text_without_the_slot():
    data = load_template("then-i-am").model_dump()
    data["judge_out_text"] = "The judge is out."
    with pytest.raises(ValidationError, match="standing_form"):
        Template.model_validate(data)


def test_demo_round_carries_both_moves_points_and_openings_from_the_seed_pool():
    template = load_template("then-i-am")
    demo = template_view(template, featured=False).demo
    assert [[p.earned for p in m.points or []] for m in demo.moves] == [[15, 12, 0], [20, 12, 4]]
    assert all(template.seed_named(o.token) for o in demo.openings)
    assert demo.openings[0].examples


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
    flags = {slug: template_view(t, False).medallions for slug, t in load_templates().items()}
    assert flags["then-i-am"] and not flags["domino"] and not flags["alibi"]


def test_lint_rejects_a_demo_opening_outside_the_seed_pool():
    data = load_template("then-i-am").model_dump()
    data["demo"]["openings"][0]["token"] = "a unicorn"
    with pytest.raises(ValidationError, match="seed pool"):
        Template.model_validate(data)


def test_every_template_on_disk_loads():
    assert set(load_templates()) >= {"then-i-am", "word-for-word"}


def test_showcase_view_carries_rounds_and_the_demo_reveal():
    view = template_view(load_template("word-for-word"), featured=False)
    assert view.mode == "showcase" and view.rounds_budget == 3
    assert view.demo.opening.detail.startswith("noun")
    assert view.demo.opening.reveal
    assert template_view(load_template("then-i-am"), featured=False).rounds_budget == 4


def test_lint_rejects_a_showcase_decided_by_sudden_death():
    data = load_template("word-for-word").model_dump()
    data["win_condition"] = "sudden_death"
    with pytest.raises(ValidationError, match="points_total"):
        Template.model_validate(data)


def test_lint_rejects_a_showcase_with_more_rounds_than_cards():
    data = load_template("word-for-word").model_dump()
    data["rounds_budget"] = len(data["seed_pool"]) + 1
    with pytest.raises(ValidationError, match="every round"):
        Template.model_validate(data)


def test_lint_rejects_a_seat_range_upside_down():
    data = load_template("then-i-am").model_dump()
    data["num_players"] = {"min": 4, "max": 3}
    with pytest.raises(ValidationError, match="min is above"):
        Template.model_validate(data)


def test_turn_games_seat_four_showcase_games_six_and_alibi_two_suspects():
    caps = {slug: t.num_players.max for slug, t in load_templates().items()}
    assert caps == {"alibi": 2, "domino": 4, "front-page": 6, "then-i-am": 4, "word-for-word": 6}


def test_card_text_pairs_the_word_with_its_detail():
    card = load_template("word-for-word").seed_named("zarf")
    assert card and card.card_text == "zarf (noun, from Arabic via Turkish)"
    assert load_template("then-i-am").seed_pool[0].card_text == "a balloon"


def test_showcase_view_carries_the_call_and_then_i_am_has_none():
    guess = template_view(load_template("word-for-word"), featured=False).guess
    assert guess is not None and guess.model_dump() == {
        "spot_points": 10,
        "fool_points": 10,
        "prompt": "One of these is the real entry. Call it.",
    }
    assert template_view(load_template("then-i-am"), featured=False).guess is None


def test_lint_rejects_a_call_without_a_hidden_truth_or_outside_a_showcase():
    data = load_template("word-for-word").model_dump()
    data["seed_pool"][0]["hidden"] = ""
    with pytest.raises(ValidationError, match="hidden truth"):
        Template.model_validate(data)
    data = load_template("then-i-am").model_dump()
    data["guess"] = {"spot_points": 10, "fool_points": 10, "prompt": "Call it."}
    with pytest.raises(ValidationError, match="showcase"):
        Template.model_validate(data)


def test_opponent_prompt_carries_premise_criterion_and_a_move_cap_below_the_real_one():
    duel = load_template("then-i-am")
    system = render_opponent_messages(duel, "a rock", [], seat="p1")[0]["content"]
    assert system.startswith(duel.premise.strip())
    assert duel.criterion.description.strip() in system
    assert duel.criterion.anti_metagaming_clause.strip() in system
    assert duel.move_constraints.max_chars == 200
    assert 'at most 160 characters and starts with "I am "' in system
    assert system.endswith(duel.opponent_prompt.strip())
    assert "{max_chars}" not in system

    words = load_template("word-for-word")
    system = render_opponent_messages(words, "zarf", [], seat="p1")[0]["content"]
    assert "at most 128 characters. Reply with the move only." in system
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
    messages = render_opponent_messages(words, "A neighbour borrowed a ladder.", earlier, seat="p1")
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[2]["content"] == "TREES"
    user = messages[3]["content"]
    assert user.index("twelve trees") < user.index("ladder") < user.index("YOUR MOVE")
    assert user.rstrip().endswith("THIS ROUND'S CARD: A neighbour borrowed a ladder.\n\nYOUR MOVE:")


def test_each_writer_call_starts_with_the_one_before_it_so_the_server_cache_extends():
    duel = load_template("then-i-am")
    first = render_opponent_messages(duel, "a rock", ["player1: I am sand"], seat="p2")
    later = ["player1: I am sand", "player2: I am glass", "player1: I am a window"]
    second = render_opponent_messages(duel, "a rock", later, seat="p2")
    assert second[: len(first)] == first
    assert second[len(first)] == {"role": "assistant", "content": "I am glass"}
    assert "I am a window" in second[-1]["content"] and "I am sand" not in second[-1]["content"]

    words = load_template("front-page")
    one = ["round 1, prompt: Card one.", "player1: ONE", "player2: UNO"]
    first = render_opponent_messages(words, "Card one.", [], seat="p2")
    second = render_opponent_messages(words, "Card two.", one, seat="p2")
    assert second[: len(first)] == first
    assert second[len(first)] == {"role": "assistant", "content": "UNO"}


def test_the_house_is_told_which_form_to_beat_and_a_fallen_move_never_stands():
    duel = load_template("then-i-am")
    first = render_opponent_messages(duel, "a rock", [], seat="p1")
    assert first[-1]["content"].endswith("Beat this: a rock\n\nYOUR MOVE:")
    lines = ["player1: I am sand", "player2: I am glass", "player3: I am a puddle"]
    fell = frozenset({"player3: I am a puddle"})
    messages = render_opponent_messages(duel, "a rock", lines, seat="p1", fell=fell)
    assert "Beat this: a rock\n" in messages[1]["content"]
    assert messages[-1]["content"].endswith("Beat this: I am glass\n\nYOUR MOVE:")


def test_the_judge_slm_sees_only_the_games_own_text_and_each_call_extends_the_last():
    duel = load_template("then-i-am")
    first = render_judge_messages(duel, [], "p1", "a rock", "I am sand")
    system = first[0]["content"]
    assert duel.premise.strip() in system and duel.host.persona_name in system
    assert all(r.name in system for r in duel.rubric)
    assert duel.examples[0].move in system
    assert "GATES" not in system and "CONFIDENCE" not in system and "OUTPUT" not in system
    earlier = JudgedTurn("p1", "a rock", "I am sand", "", '{"scoring": {}}')
    second = render_judge_messages(duel, [earlier], "p2", "I am sand", "I am glass")
    assert second[: len(first)] == first
    assert second[len(first)] == {"role": "assistant", "content": '{"scoring": {}}'}
    assert "by player2" in second[-1]["content"] and "I am glass" in second[-1]["content"]
