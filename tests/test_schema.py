import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from arena_server.db import SCHEMA_PATH

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL pointing at Postgres",
    ),
    pytest.mark.xdist_group("database"),
]

BEFORE_POINTS = Path(__file__).parent / "fixtures" / "schema_before_points.sql"
WEIGHTS = {"counter_strength": 5, "coherence": 3, "novelty": 2}


@contextmanager
def scratch_schema() -> Iterator[tuple[psycopg.Connection, str]]:
    schema = f"schema_{secrets.token_hex(4)}"
    name = sql.Identifier(schema)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(name))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(name))
            yield conn, schema
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(name))


def boot(conn: psycopg.Connection, path: Path = SCHEMA_PATH) -> None:
    conn.execute(sql.SQL(path.read_text()))  # type: ignore[arg-type]


def shape(conn: psycopg.Connection, schema: str) -> dict[str, list[tuple]]:
    """Columns, indexes and constraints, with the schema name taken out."""
    queries = {
        "columns": "select table_name, column_name, data_type, is_nullable, column_default "
        "from information_schema.columns where table_schema = %(s)s",
        "indexes": "select tablename, indexname, indexdef from pg_indexes where schemaname = %(s)s",
        "constraints": "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
        "from pg_constraint where connamespace = %(s)s::regnamespace",
    }
    return {
        key: sorted(
            tuple(str(v).replace(f"{schema}.", "") for v in row)
            for row in conn.execute(query, {"s": schema})  # type: ignore[arg-type]
        )
        for key, query in queries.items()
    }


def test_a_fresh_database_takes_the_schema_twice():
    with scratch_schema() as (conn, schema):
        boot(conn)
        first = shape(conn, schema)
        boot(conn)
        assert shape(conn, schema) == first
        assert ("turns", "points", "integer", "YES", "None") in first["columns"]


def test_a_database_in_the_old_shape_upgrades_once_and_keeps_each_turns_points():
    with scratch_schema() as (conn, schema):
        boot(conn)
        fresh = shape(conn, schema)
    with scratch_schema() as (conn, schema):
        boot(conn, BEFORE_POINTS)
        rubric = [{"name": n, "weight": w} for n, w in WEIGHTS.items()]
        fractional = [{"name": n, "weight": w / 10} for n, w in WEIGHTS.items()]
        conn.execute("insert into sessions (session_key) values ('s1')")
        conn.execute(
            "insert into matches (id, template_id, template_version, config, seed_token, "
            "seed_emoji, cards, status, created_at) values "
            "('old', 'then-i-am', 1, %s, 'a rock', '🪨', '{a rock}', 'ended', '2026-01-01'), "
            "('new', 'then-i-am', 1, %s, 'a lock', '🔒', '{a lock}', 'ended', '2026-02-01')",
            (json.dumps({"rubric": fractional}), json.dumps({"rubric": rubric})),
        )
        moves = [
            ("new", 1, "accept", {"counter_strength": 3, "coherence": 3, "novelty": 2}),
            ("new", 2, "fail", {"counter_strength": 3, "coherence": 3, "novelty": 2}),
            ("new", 3, "semantic_uncertain", {"counter_strength": 1, "coherence": 2, "retired": 4}),
            ("new", None, "semantic_reject", {"counter_strength": 0, "coherence": 1, "novelty": 0}),
            ("old", 1, "accept", {"counter_strength": 3, "coherence": 3, "novelty": 2}),
        ]
        for match_id, seq, outcome, scores in moves:
            conn.execute(
                "with v as (insert into verdicts (judge_model, prompt_hash, raw_response, "
                "scoring, latency_ms) values ('judge', 'h', '', %s, 1) returning id) "
                "insert into turns (match_id, seq, actor, move_text, outcome, live_verdict_id) "
                "select %s, %s, 'p1', 'a move', %s, id from v",
                (json.dumps({"scores": scores}), match_id, seq, outcome),
            )
        conn.execute(
            "insert into turns (match_id, seq, actor, move_text, outcome) "
            "values ('new', 4, 'p2', '', 'forfeit')"
        )

        boot(conn)
        boot(conn)

        points = conn.execute(
            "select match_id, seq, points from turns order by match_id, seq nulls last"
        ).fetchall()
        assert points == [
            ("new", 1, 15 + 9 + 4),
            ("new", 2, 0),
            ("new", 3, 5 + 6),
            ("new", 4, None),
            ("new", None, None),
            ("old", 1, 15 + 9 + 4),
        ]
        assert conn.execute("select seed_emoji from matches order by id").fetchall() == [
            ("🔒",),
            ("🪨",),
        ]
        assert shape(conn, schema) == fresh


def test_an_upgrade_that_finds_no_rubric_for_a_match_stops_and_changes_nothing():
    with scratch_schema() as (conn, _):
        boot(conn, BEFORE_POINTS)
        fractional = [{"name": n, "weight": w / 10} for n, w in WEIGHTS.items()]
        conn.execute(
            "insert into matches (id, template_id, template_version, config, seed_token, "
            "seed_emoji, cards, status) values "
            "('odd', 'then-i-am', 1, %s, 'a rock', '🪨', '{a rock}', 'ended')",
            (json.dumps({"rubric": fractional}),),
        )
        conn.execute(
            "with v as (insert into verdicts (judge_model, prompt_hash, raw_response, "
            "scoring, latency_ms) values ('judge', 'h', '', %s, 1) returning id) "
            "insert into turns (match_id, seq, actor, move_text, outcome, live_verdict_id) "
            "select 'odd', 1, 'p1', 'a move', 'accept', id from v",
            (json.dumps({"scores": {"counter_strength": 3}}),),
        )

        with pytest.raises(
            psycopg.errors.RaiseException, match="no rubric with whole-number weights.*odd"
        ):
            boot(conn)

        assert conn.execute(
            "select 1 from information_schema.columns where table_schema = current_schema() "
            "and table_name = 'matches' and column_name = 'seed_token'"
        ).fetchone()
