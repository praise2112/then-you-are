import argparse
import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from arena_core.template import load_template
from arena_evals.datagen import flashmine
from arena_evals.datagen.ledger import CallRow, Ledger
from arena_evals.datagen.prefset import Position
from arena_judge.caller import CallError, CallResult, ModelSpec
from arena_judge.prompt import render_judge_messages
from tests.conftest import judge_response
from tests.test_datagen_play import ScriptedCaller

DUEL = load_template("then-i-am")
STRONG = judge_response(scores={"counter_strength": 4, "coherence": 4, "novelty": 4})
WEAK = judge_response(scores={"counter_strength": 1, "coherence": 1, "novelty": 1})
TRANSCRIPT = ["player1: a rock"]


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch) -> Path:
    """One mining position, at the teacher's judged move "I am rust" after the card "a rock"."""
    teacher = Ledger(tmp_path / "corpus.db")
    teacher.add_call(
        CallRow(
            "m",
            1,
            "judge",
            "p2",
            2,
            "flash",
            "",
            "",
            None,
            {
                "previous": "a rock",
                "move": "I am rust",
                "hidden": "",
                "transcript": TRANSCRIPT,
                "response": STRONG.model_dump(),
            },
            0,
            0,
            0.0,
            0,
            "parsed",
        )
    )
    teacher.close()
    position = Position(
        id="m/2",
        template_id="then-i-am",
        player_messages=[{"role": "user", "content": "beat a rock"}],
        judge_messages=render_judge_messages(DUEL, [], "p2", "a rock", "I am rust"),
        teacher_move="I am rust",
        on_table=["a rock"],
    )
    (tmp_path / "mined.positions.jsonl").write_text(position.model_dump_json() + "\n")
    monkeypatch.setattr(flashmine, "CORPUS_DIR", tmp_path)
    monkeypatch.setattr(flashmine, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(flashmine, "JUDGE_RUNS", ("corpus",))
    monkeypatch.setattr(flashmine, "corpus_games", lambda _: ({"then-i-am": DUEL}, {}))
    monkeypatch.setattr(flashmine, "load_model", lambda ref: ModelSpec(model=ref, display_name=ref))
    return tmp_path


def mine(corpus: Path, caller: ScriptedCaller, monkeypatch) -> dict[str, list[dict]]:
    monkeypatch.setattr(flashmine, "make_caller", lambda **_: caller)
    args = argparse.Namespace(
        name="mined", player="http://player", budget=1.0, limit=None, concurrency=1
    )
    asyncio.run(flashmine.main(args))
    return {
        suffix: [json.loads(line) for line in (corpus / f"mined.{suffix}.jsonl").open()]
        for suffix in ("flash-judge", "flash-scored", "flash-pairs", "flash-refused-pairs")
    }


def test_each_draw_is_judged_once_and_a_missing_verdict_skips_only_that_draw(
    corpus: Path, monkeypatch
):
    caller = ScriptedCaller(
        rulings=[STRONG, None, WEAK],
        moves=["I am a hammer", "I am a flood", "a rock", "I am a lever"],
    )
    out = mine(corpus, caller, monkeypatch)

    assert caller.judged == ["I am a hammer", "I am a flood", "I am a lever"]
    assert [(s["move"], s["totals"], s["passes"]) for s in out["flash-scored"]] == [
        ("I am a hammer", [40], True),
        ("I am a lever", [10], True),
    ]
    assert out["flash-judge"][0]["messages"][-1]["content"].endswith(
        "<move>\nI am a hammer\n</move>"
    )
    assert [(p["chosen"], p["rejected"], p["margin"]) for p in out["flash-pairs"]] == [
        ("I am a hammer", "I am a lever", 30)
    ]
    assert [(p["chosen"], p["rejected"]) for p in out["flash-refused-pairs"]] == [
        ("I am a hammer", "a rock")
    ]


def test_a_rerun_replays_the_ledger_and_pays_only_for_the_missing_verdict(
    corpus: Path, monkeypatch
):
    moves = ["I am a hammer", "I am a flood", "a rock", "I am a lever"]
    mine(corpus, ScriptedCaller(rulings=[STRONG, None, WEAK], moves=moves), monkeypatch)

    rerun = ScriptedCaller(rulings=[WEAK], moves=[])
    out = mine(corpus, rerun, monkeypatch)

    assert rerun.completed == 0 and rerun.judged == ["I am a flood"]
    assert [s["move"] for s in out["flash-scored"]] == [
        "I am a hammer",
        "I am a flood",
        "I am a lever",
    ]


def add_second_position(corpus: Path) -> None:
    """A copy of the mining position under another match, so a run has two positions."""
    teacher = Ledger(corpus / "corpus.db")
    teacher.add_call(dataclasses.replace(teacher.calls("m")[0], match_id="n"))
    teacher.close()
    path = corpus / "mined.positions.jsonl"
    first = Position.model_validate_json(path.read_text())
    path.write_text(path.read_text() + first.model_copy(update={"id": "n/2"}).model_dump_json())


class NoCreditWriter(ScriptedCaller):
    async def complete(self, spec, messages, **extra) -> CallResult:
        self.completed += 1
        raise CallError("402 Payment Required", 402)


def test_an_out_of_credit_writer_stops_the_remaining_positions(corpus: Path, monkeypatch):
    add_second_position(corpus)
    caller = NoCreditWriter(rulings=[], moves=[])
    with pytest.raises(SystemExit, match="out of credit"):
        mine(corpus, caller, monkeypatch)
    assert caller.completed == 1 and caller.judged == []


def test_an_out_of_credit_judge_stops_the_remaining_draws_and_positions(corpus: Path, monkeypatch):
    add_second_position(corpus)
    caller = ScriptedCaller(rulings=[402], moves=["I am a hammer"])
    with pytest.raises(SystemExit, match="out of credit"):
        mine(corpus, caller, monkeypatch)
    assert caller.completed == 1 and caller.judged == ["I am a hammer"]
