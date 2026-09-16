import asyncio
import json
from pathlib import Path

import pytest

from arena_core.template import load_template
from arena_evals.datagen.ledger import Ledger
from arena_evals.datagen.play import play_match
from arena_evals.datagen.records import RenderMismatch, export, quantile_of, terciles
from arena_judge.prompt import render_opponent_messages
from tests.conftest import judge_response
from tests.test_datagen_play import JUDGE, SIDES, ScriptedCaller, duel_match, word_match

TEMPLATES = {
    "then-i-am": load_template("then-i-am"),
    "word-for-word": load_template("word-for-word"),
}
CLASSES = {"then-i-am": "counter", "word-for-word": "showcase"}


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def run_duel(tmp_path: Path, rulings, moves) -> tuple[Ledger, Path]:
    ledger = Ledger(tmp_path / "run.db")
    asyncio.run(
        play_match(
            TEMPLATES["then-i-am"],
            duel_match(),
            SIDES,
            ScriptedCaller(rulings, moves),
            ledger,
            JUDGE,
        )
    )
    return ledger, tmp_path / "corpus"


def test_accepted_moves_become_player_records_rendered_like_runtime(tmp_path: Path):
    rulings = [
        judge_response(),
        judge_response(confidence="coin_flip", verdict="fail"),
        judge_response(verdict="fail"),
    ]
    moves = ["I am a hammer, rock-splitting.", "I am rust, hinge-eating.", "I am a poem."]
    ledger, out = run_duel(tmp_path, rulings, moves)
    result = export(ledger, TEMPLATES, CLASSES, out)
    players = read(out / "player.jsonl")
    assert result.player == 2 and [p["target"] for p in players] == moves[:2]
    first = players[0]
    assert first["messages"] == render_opponent_messages(TEMPLATES["then-i-am"], "a rock", [])
    prov = first["provenance"]
    assert prov["teacher"] == "opponent-v1" and prov["game_class"] == "counter"
    assert prov["judge_model"] == "flash-judge" and prov["target_quality"] == "best"
    assert prov["card"] == "a rock" and prov["transcript"] == []
    assert players[1]["provenance"]["outcome"] == "semantic_uncertain"
    assert players[1]["provenance"]["transcript"] == ["player1: I am a hammer, rock-splitting."]
    assert result.drops == {
        "player: not accepted": 1,
        "host: fail narrated on a move that stood": 1,
    }
    assert result.judge == 3 and result.host == 2
    judge = read(out / "judge.jsonl")[2]
    assert judge["outcome"] == "fail" and judge["prompt"].endswith("I am a poem.\n</move>")
    hosts = read(out / "host.jsonl")
    assert [h["seq"] for h in hosts] == [1, 3] and hosts[1]["input"]["outcome"] == "fail"


def test_truth_hits_salvaged_verdicts_and_default_moves_stay_out(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    rulings = [judge_response(truth_proximity="hit"), judge_response()] + [judge_response()] * 5
    caller = ScriptedCaller(rulings, [f"a thing {i}" for i in range(7)])
    asyncio.run(play_match(TEMPLATES["word-for-word"], word_match(), SIDES, caller, ledger, JUDGE))
    ledger.conn.execute("update calls set attempt = 'salvaged' where role = 'judge' and seq = 3")
    ledger.conn.commit()
    result = export(ledger, TEMPLATES, CLASSES, tmp_path / "corpus")
    assert result.drops == {"player: truth hit": 1, "player: salvaged verdict": 1}
    assert result.player == 5 and result.host == 6
    hidden = {p["provenance"]["hidden"] for p in read(tmp_path / "corpus" / "player.jsonl")}
    assert all(hidden)


def test_golden_pairs_are_dropped_as_contamination(tmp_path: Path):
    rulings = [judge_response(), judge_response(), judge_response(verdict="fail")]
    moves = [
        "I am salt, ice-melting, road-spreading.",
        "I am a bigger pile of salt, saltier.",
        "I am a poem.",
    ]
    ledger, out = run_duel(tmp_path, rulings, moves)
    result = export(ledger, TEMPLATES, CLASSES, out)
    assert result.drops["player: golden contamination"] == 1 and result.player == 1


def test_export_refuses_a_prompt_the_serving_code_no_longer_renders(tmp_path: Path):
    ledger, out = run_duel(
        tmp_path, [judge_response(verdict="fail")], ["I am a hammer, rock-splitting."]
    )
    ledger.conn.execute(
        "update calls set payload = replace(payload, 'Two shapeshifters', 'Two wizards') "
        "where role = 'move'"
    )
    ledger.conn.commit()
    ledger.conn.execute(
        "update calls set payload = "
        """replace(payload, '"verdict": "fail"', '"verdict": "accept"')"""
    )
    ledger.conn.commit()
    with pytest.raises(RenderMismatch):
        export(ledger, TEMPLATES, CLASSES, out)


def test_terciles_rank_within_the_batch_and_need_three_scores():
    assert terciles([10, 20]) is None
    cuts = terciles([10, 20, 30, 40, 50, 60])
    assert (
        quantile_of(10, cuts) == "low"
        and quantile_of(35, cuts) == "mid"
        and quantile_of(60, cuts) == "high"
    )
    assert quantile_of(10, None) is None
