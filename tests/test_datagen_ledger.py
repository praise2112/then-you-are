import asyncio
from pathlib import Path

import pytest

from arena_core.template import load_template
from arena_evals.datagen.ledger import CallFailed, JudgeInputs, Ledger, Tape
from arena_judge.caller import ModelSpec
from tests.conftest import FakeCaller, judge_response

DUEL = load_template("then-i-am")
JUDGE = ModelSpec(model="flash-judge", display_name="The Judge")
ASK = JudgeInputs("a rock", "I am a hammer, rock-splitting.", "", ["player1: a rock"])


def judge(ledger: Ledger, caller: FakeCaller, ask: JudgeInputs = ASK):
    return asyncio.run(Tape(ledger, "m/1").judge(caller, JUDGE, DUEL, "p1", 1, ask))


def test_a_recorded_verdict_replays_without_a_live_call_and_leaves_the_ledger_alone(
    tmp_path: Path,
):
    ledger = Ledger(tmp_path / "run.db")
    first = judge(ledger, FakeCaller([judge_response(verdict="fail")], []))
    recorded = ledger.calls("m/1")

    replayer = FakeCaller([], [])
    again = judge(ledger, replayer)

    assert again == first and again.scoring.verdict == "fail"
    assert replayer.judged == []
    assert ledger.calls("m/1") == recorded
    assert recorded[0].payload["move"] == ASK.move and recorded[0].verdict == first


def test_a_replay_asked_about_another_move_refuses_to_continue(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    judge(ledger, FakeCaller([judge_response()], []))
    other = JudgeInputs(ASK.previous, "I am glue.", ASK.hidden, ASK.transcript)
    with pytest.raises(RuntimeError, match="new inputs"):
        judge(ledger, FakeCaller([], []), other)


def test_no_verdict_raises_and_the_next_run_asks_again_keeping_the_first_cost(
    tmp_path: Path,
):
    ledger = Ledger(tmp_path / "run.db")
    with pytest.raises(CallFailed):
        judge(ledger, FakeCaller([None], []))
    assert ledger.calls("m/1")[0].verdict is None

    back = FakeCaller([judge_response()], [])
    assert judge(ledger, back).scoring.verdict == "accept"
    assert back.judged == [ASK.move] and len(ledger.calls("m/1")) == 1
