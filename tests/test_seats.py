import os
import secrets
from typing import LiteralString

import psycopg
import pytest
from psycopg import sql

from arena_core.state import Match, apply_guess, apply_ruling, resign, transcript
from arena_core.template import Template, load_template
from arena_judge.schema import Outcome
from arena_server.db import SCHEMA_PATH

DUEL = load_template("then-i-am")
WORDS = load_template("word-for-word")


def table(*seats: str, cards: tuple[str, ...] = ("a rock",), **kw) -> Match:
    return Match(
        id="t", template_id="then-i-am", template_version=1, cards=list(cards), seats=seats, **kw
    )


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
        template_version=1,
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


OLD_SHAPE: LiteralString = """
create table sessions (session_key text primary key, stage_name text not null default 'Challenger',
    created_at timestamptz not null default now());
create table matches (id text primary key, template_id text not null, template_version int not null,
    config jsonb not null, seed_token text not null, seed_emoji text not null,
    p1_session_key text not null references sessions(session_key), p2_model_ref text not null,
    status text not null, state_version int not null default 0, to_move text not null default 'p1',
    winner text, end_reason text, points_p1 integer not null default 0,
    points_p2 integer not null default 0, strikes_p1 int not null default 0,
    strikes_p2 int not null default 0, is_public boolean not null default true,
    is_curated boolean not null default false, created_at timestamptz not null default now(),
    ended_at timestamptz, phase text not null default 'write');
create table turns (id bigserial primary key, match_id text not null references matches(id),
    seq int, actor text not null, move_text text not null, layer1_result text,
    outcome text not null, live_verdict_id bigint, action_id text,
    created_at timestamptz not null default now(), round_n int not null default 1,
    unique (match_id, action_id));
insert into sessions (session_key) values ('s1');
insert into matches (id, template_id, template_version, config, seed_token, seed_emoji,
    p1_session_key, p2_model_ref, status, points_p1, points_p2, strikes_p1, phase)
values ('words', 'word-for-word', 1, '{"move_budget": 6}', 'zarf', '', 's1', 'opponent', 'active',
    30, 20, 1, 'guess'),
    ('duel', 'then-i-am', 1, '{"move_budget": 10}', 'a rock', '', 's1', 'opponent', 'active',
    40, 35, 0, 'write');
insert into turns (match_id, seq, actor, move_text, outcome) values
    ('words', 1, 'p1', 'a', 'accept'), ('words', 2, 'p2', 'b', 'fail'),
    ('duel', 1, 'p1', 'a', 'accept'), ('duel', 2, 'p2', 'b', 'accept'),
    ('duel', 3, 'p1', 'c', 'accept'), ('duel', null, 'p2', 'x', 'semantic_reject');
"""


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL pointing at Postgres"
)
@pytest.mark.xdist_group("database")
def test_old_matches_move_into_seats_and_boot_twice():
    schema = f"migration_{secrets.token_hex(4)}"
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        name = sql.Identifier(schema)
        conn.execute(sql.SQL("create schema {}").format(name))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(name))
            conn.execute(OLD_SHAPE)
            boot = sql.SQL(SCHEMA_PATH.read_text())  # type: ignore[arg-type]
            conn.execute(boot)
            conn.execute(boot)
            seats = conn.execute(
                "select match_id, seat, kind, session_key, model_ref, points, strikes from seats "
                "order by match_id, seat"
            ).fetchall()
            assert seats == [
                ("duel", "p1", "human", "s1", None, 40, 0),
                ("duel", "p2", "model", None, "opponent", 35, 0),
                ("words", "p1", "human", "s1", None, 30, 1),
                ("words", "p2", "model", None, "opponent", 20, 0),
            ]
            matches = conn.execute("select id, round_n, config from matches order by id").fetchall()
            assert matches == [
                ("duel", 2, {"rounds_budget": 5}),
                ("words", 1, {"rounds_budget": 3}),
            ]
            columns = {
                r[0]
                for r in conn.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema = %s and table_name = 'matches'",
                    (schema,),
                )
            }
            assert not columns & {"p1_session_key", "p2_model_ref", "points_p1", "strikes_p2"}
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(name))
