import pytest

from arena_core.state import (
    Match,
    Outcome,
    apply_guess,
    apply_ruling,
    forfeit_turn,
    resign,
    skip_guess,
    transcript,
)
from arena_core.template import Template, load_template

DUEL = load_template("then-i-am")
WORDS = load_template("word-for-word")


def table(*seats: str, cards: tuple[str, ...] = ("a rock",), **kw) -> Match:
    return Match(id="t", template_id="then-i-am", cards=list(cards), seats=seats, **kw)


def rounds(template: Template, n: int) -> Template:
    return template.model_copy(update={"rounds_budget": n})


def play(match: Match, template: Template, *moves: tuple[str, Outcome, int]) -> None:
    for actor, outcome, points in moves:
        apply_ruling(
            match,
            actor,
            f"I am {actor} {len(match.turns)}",
            outcome,
            match.state_version,
            template,
            points,
        )


def test_turns_go_round_the_table_and_a_wrap_starts_the_next_round():
    match = table("p1", "p2", "p3")
    play(match, DUEL, ("p1", "accept", 0), ("p2", "accept", 0))
    assert (match.to_move, match.round_n) == ("p3", 1)
    play(match, DUEL, ("p3", "accept", 0))
    assert (match.to_move, match.round_n) == ("p1", 2)
    assert [t.round_n for t in match.turns] == [1, 1, 1]


def test_a_fail_knocks_the_mover_out_and_the_next_seat_answers_the_same_form():
    match = table("p1", "p2", "p3", "p4")
    play(match, DUEL, ("p1", "accept", 10), ("p2", "fail", 0))
    assert match.status == "active" and match.eliminated == ["p2"]
    assert match.to_move == "p3" and match.standing_form == "I am p1 0"
    play(match, DUEL, ("p3", "accept", 10), ("p4", "fail", 0))
    assert (match.to_move, match.round_n) == ("p1", 2)
    play(match, DUEL, ("p1", "fail", 0))
    assert match.status == "ended" and match.end_reason == "sudden_death"
    assert match.winner == "p3" and match.live_seats == ["p3"]


def test_a_knocked_out_seat_keeps_its_points_but_cannot_win_on_budget():
    match = table("p1", "p2", "p3")
    play(match, rounds(DUEL, 2), ("p1", "accept", 10), ("p2", "accept", 30), ("p3", "accept", 20))
    play(match, rounds(DUEL, 2), ("p1", "accept", 10), ("p2", "fail", 0), ("p3", "accept", 5))
    assert match.status == "ended" and match.end_reason == "move_cap_points"
    assert match.points == {"p1": 20, "p2": 30, "p3": 25}
    assert match.winner == "p3"


def test_a_tie_at_the_top_goes_to_the_tied_seat_that_moved_last():
    match = table("p1", "p2", "p3")
    play(match, rounds(DUEL, 1), ("p1", "accept", 20), ("p2", "accept", 20), ("p3", "accept", 10))
    assert match.status == "ended" and match.winner == "p2"


def test_a_seat_that_resigns_leaves_and_play_goes_on_until_one_is_left():
    match = table("p1", "p2", "p3")
    play(match, DUEL, ("p1", "accept", 0))
    resign(match, "p2", match.state_version, DUEL)
    assert match.status == "active" and (match.to_move, match.round_n) == ("p3", 1)
    resign(match, "p3", match.state_version, DUEL)
    assert match.status == "ended" and match.end_reason == "resign" and match.winner == "p1"


def test_a_showcase_round_waits_for_all_six_seats_then_each_guesser_calls():
    seats = ("p1", "p2", "p3", "p4", "p5", "p6")
    match = Match(
        id="w",
        template_id="word-for-word",
        cards=["zarf", "groak", "oxter"],
        seats=seats,
        guessers=("p1", "p3"),
    )
    for seat in seats[:5]:
        apply_ruling(match, seat, f"bluff {seat}", "accept", match.state_version, WORDS, points=10)
    assert match.phase == "write" and match.round_n == 1
    with pytest.raises(ValueError, match="already answered"):
        apply_ruling(match, "p2", "again", "accept", match.state_version, WORDS, points=10)
    apply_ruling(match, "p6", "bluff p6", "accept", match.state_version, WORDS, points=10)
    assert match.phase == "guess" and match.owed_guesses() == ["p1", "p3"]
    assert match.guess_options("p1") == ["truth", "p2", "p3", "p4", "p5", "p6"]
    apply_guess(match, "p1", "p4", match.state_version, WORDS)
    apply_guess(match, "p3", "truth", match.state_version, WORDS)
    assert (match.phase, match.round_n, match.card) == ("write", 2, "groak")
    assert WORDS.guess is not None
    assert match.points["p4"] == 10 + WORDS.guess.fool_points
    assert match.points["p3"] == 10 + WORDS.guess.spot_points


def test_a_forfeit_passes_the_turn_and_a_second_one_puts_the_seat_out():
    match = table("p1", "p2", "p3")
    play(match, DUEL, ("p1", "accept", 10))
    forfeit_turn(match, "p2", DUEL)
    assert match.to_move == "p3" and match.standing_form == "I am p1 0"
    assert match.eliminated == [] and match.forfeits["p2"] == 1
    play(match, DUEL, ("p3", "accept", 0), ("p1", "accept", 0))
    forfeit_turn(match, "p2", DUEL)
    assert match.eliminated == ["p2"] and match.to_move == "p3"
    assert "player2" not in " ".join(transcript(match, DUEL))


def test_the_last_seat_standing_after_forfeits_wins():
    match = table("p1", "p2")
    forfeit_turn(match, "p1", DUEL)
    play(match, DUEL, ("p2", "accept", 0))
    forfeit_turn(match, "p1", DUEL)
    assert (match.status, match.end_reason, match.winner) == ("ended", "forfeit", "p2")


def test_a_forfeited_answer_closes_the_round_and_leaves_no_bluff_to_pick():
    match = Match(
        id="w",
        template_id="word-for-word",
        cards=["zarf", "groak", "oxter"],
        seats=("p1", "p2", "p3"),
        guessers=("p1", "p2"),
    )
    apply_ruling(match, "p1", "a cloak", "accept", 0, WORDS, points=10)
    apply_ruling(match, "p3", "a hat", "accept", 1, WORDS, points=10)
    forfeit_turn(match, "p2", WORDS)
    assert match.phase == "guess" and match.guess_options("p1") == ["truth", "p3"]
    skip_guess(match, "p2", WORDS)
    assert match.owed_guesses() == ["p1"]
    apply_guess(match, "p1", "truth", match.state_version, WORDS)
    assert (match.phase, match.round_n) == ("write", 2)
    assert [g.picked for g in match.guesses] == ["none", "truth"]
    assert match.points["p2"] == 0


def test_strikes_reset_once_a_move_is_judged():
    match = table("p1", "p2")
    apply_ruling(match, "p1", "lol", "semantic_reject", 0, DUEL)
    apply_ruling(match, "p1", "lol again", "semantic_reject", 1, DUEL)
    assert match.strikes["p1"] == 2
    play(match, DUEL, ("p1", "accept", 0))
    assert match.strikes["p1"] == 0


def test_a_table_still_filling_takes_no_moves():
    match = table("p1", "p2", status="open")
    with pytest.raises(ValueError, match="still filling"):
        play(match, DUEL, ("p1", "accept", 0))


def test_the_transcript_names_every_seat():
    match = table("p1", "p2", "p3")
    play(match, DUEL, ("p1", "accept", 0), ("p2", "accept", 0), ("p3", "accept", 0))
    assert [line.split(":")[0] for line in transcript(match, DUEL)] == [
        "player1",
        "player2",
        "player3",
    ]


@pytest.mark.parametrize("seats", [("p1",), tuple(f"p{n}" for n in range(1, 8))])
def test_a_table_seats_two_to_six(seats):
    with pytest.raises(ValueError, match="2 to 6"):
        table(*seats)


def test_a_forfeited_round_still_counts_as_history_for_the_prompts():
    match = Match(
        id="f",
        template_id="front-page",
        cards=["zarf", "groak", "oxter"],
        seats=("p1", "p2", "p3"),
        guessers=(),
    )
    apply_ruling(match, "p1", "a headline", "accept", 0, WORDS, points=10)
    apply_ruling(match, "p2", "another headline", "accept", 1, WORDS, points=10)
    forfeit_turn(match, "p3", WORDS)
    assert match.round_n == 2
    assert transcript(match, WORDS, finished_only=True)[1:] == [
        "player1: a headline",
        "player2: another headline",
    ]
