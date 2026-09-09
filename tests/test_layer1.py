from arena_core.state import Match, apply_ruling, layer1


def new_match() -> Match:
    return Match(id="m", template_id="then-i-am", template_version=1, cards=["a rock"])


def test_empty_move_is_refused(template):
    assert layer1(template, "   ", new_match()) == "empty"


def test_move_over_the_cap_is_refused(template):
    assert layer1(template, "I am " + "x" * 200, new_match()) == "too_long"


def test_becoming_the_seed_is_a_duplicate(template):
    assert layer1(template, "A rock!", new_match()) == "duplicate"


def test_repeating_a_standing_form_is_a_duplicate(template):
    match = new_match()
    apply_ruling(match, "p1", "I am rust, patient.", "accept", 0, template)
    assert layer1(template, "i am rust, patient", match) == "duplicate"
    assert layer1(template, "I am oil, rust-stopping.", match) is None


def test_punctuation_only_is_empty(template):
    assert layer1(template, "??? !!! ...", new_match()) == "empty"
