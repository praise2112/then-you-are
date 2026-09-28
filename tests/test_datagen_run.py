import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arena_core.template import load_template
from arena_evals import common
from arena_evals.datagen import card, run
from arena_evals.datagen.ledger import CallRow, Ledger
from arena_evals.variants.generate import Cell, Entry
from arena_evals.variants.spec import load_spec
from arena_judge.caller import JudgeCall, ModelSpec
from tests.conftest import judge_response
from tests.test_datagen_play import NoCredit, ScriptedCaller

TEMPLATES = {"then-i-am": load_template("then-i-am")}


def test_the_window_guard_knows_the_off_peak_hours():
    assert common.in_window(datetime(2026, 9, 16, 17, 0, tzinfo=UTC))
    assert common.in_window(datetime(2026, 9, 16, 0, 10, tzinfo=UTC))
    assert not common.in_window(datetime(2026, 9, 16, 12, 0, tzinfo=UTC))
    wait = common.seconds_until_open(datetime(2026, 9, 16, 12, 0, tzinfo=UTC))
    assert wait == 4.5 * 3600


def test_the_estimate_prices_peak_above_off_peak():
    off_peak, peak = run.estimate(TEMPLATES, {"then-i-am": 100})
    assert 0.5 < off_peak < 2 and 2 < peak / off_peak < 2.7


async def fake_credit(caller) -> float:
    return 100.0


def test_a_run_stops_at_the_budget_then_resumes_and_reports(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(run, "load_model", lambda ref: ModelSpec(model=ref, display_name=ref))
    ledger = Ledger(tmp_path / "r.db")
    ledger.create_run(
        "r",
        {
            "matches": {"then-i-am": 3},
            "provider": "fireworks",
            "judge_ref": "judge-fireworks",
            "flash_ref": "opponent-fireworks",
            "classes": {"then-i-am": "counter"},
            "teachers": {"opponent-fireworks": run.FLASH_SHARE, run.LUNA: 1 - run.FLASH_SHARE},
            "judge_prompt_hash": {"then-i-am": "x"},
        },
    )
    run.plan_matches(ledger, TEMPLATES, {"then-i-am": 3}, random.Random(1), "opponent-fireworks")
    assert len(ledger.matches("active")) == 3

    def scripted() -> ScriptedCaller:
        moves = [f"I am thing {i}, doing." for i in range(40)]
        rulings = [judge_response(), judge_response(verdict="fail")] * 20
        caller = ScriptedCaller(rulings, moves)
        return caller

    monkeypatch.setattr(run, "make_caller", lambda judge_ref: scripted())
    monkeypatch.setattr(run, "credit_left", fake_credit)

    sabotaged: list[str] = []

    class CountingSabotage:
        def __init__(self, template, match_id, *args, **kwargs):
            self.match_id = match_id

        async def run(self) -> int:
            sabotaged.append(self.match_id)
            return 2

    monkeypatch.setattr(run, "Saboteur", CountingSabotage)
    asyncio.run(run.drive("r", ledger, TEMPLATES, budget=0.0025, now=True, concurrency=1))
    ended = ledger.matches("ended")
    assert 1 <= len(ended) < 3, "the budget stopped the run at a match boundary"
    assert (
        ledger.plan("r") is not None
        and not ledger.conn.execute("select finished from runs").fetchone()["finished"]
    )

    asyncio.run(run.drive("r", ledger, TEMPLATES, budget=10, now=True, concurrency=4))
    assert len(ledger.matches("ended")) == 3
    assert ledger.conn.execute("select finished from runs").fetchone()["finished"]

    assert sorted(sabotaged) == sorted(m["match_id"] for m in ledger.matches("ended"))
    assert all(json.loads(m["outcome"])["sabotage"] == 2 for m in ledger.matches("ended"))

    # A match whose sabotage pass never finished is picked up again without replaying.
    victim = ledger.matches("ended")[0]["match_id"]
    ledger.conn.execute(
        "update matches set outcome = json_remove(outcome, '$.sabotage') where match_id = ?",
        (victim,),
    )
    ledger.conn.commit()
    assert [m["match_id"] for m in ledger.ended_without_sabotage()] == [victim]
    asyncio.run(run.drive("r", ledger, TEMPLATES, budget=10, now=True, concurrency=4))
    assert sabotaged[-1] == victim and not ledger.ended_without_sabotage()

    text = card.write("r", ledger, tmp_path / "corpus", TEMPLATES)
    assert "then-i-am: 3 ended" in text
    assert "player.jsonl: 3" in text and "Spearman" in text
    assert (tmp_path / "corpus" / "player.jsonl").exists()


def test_the_lane_ceiling_caps_a_run_before_it_starts(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(run, "LANE_CEILING", 0.0)
    ledger = Ledger(tmp_path / "r.db")
    ledger.create_run(
        "r",
        {
            "matches": {"then-i-am": 1},
            "provider": "fireworks",
            "judge_ref": "judge-fireworks",
            "flash_ref": "opponent-fireworks",
            "classes": {"then-i-am": "counter"},
        },
    )
    run.plan_matches(ledger, TEMPLATES, {"then-i-am": 1}, random.Random(1), "opponent-fireworks")
    caller = ScriptedCaller([], [])
    monkeypatch.setattr(run, "make_caller", lambda judge_ref: caller)
    monkeypatch.setattr(run, "credit_left", fake_credit)
    monkeypatch.setattr(run, "load_model", lambda ref: ModelSpec(model=ref, display_name=ref))
    asyncio.run(run.drive("r", ledger, TEMPLATES, budget=5, now=True, concurrency=1))
    assert caller.completed == 0 and len(ledger.matches("active")) == 1
    assert "budget reached" in capsys.readouterr().err


def test_a_judge_call_that_timed_out_is_retried_and_every_attempt_is_billed(monkeypatch):
    calls: list[int] = []

    class Flaky(ScriptedCaller):
        async def judge(self, template, transcript, previous, move, hidden="", spec=None):
            calls.append(1)
            if len(calls) < 3:
                return JudgeCall(
                    response=None,
                    raw="",
                    prompt_hash="t",
                    cost_usd=0.001,
                    attempts=["call_error: timeout"],
                    error_status=None,
                )
            call = await super().judge(template, transcript, previous, move, hidden, spec)
            call.cost_usd = 0.002
            return call

    async def no_sleep(_):
        return None

    monkeypatch.setattr(common.asyncio, "sleep", no_sleep)
    caller = Flaky([judge_response()], [])
    duel = TEMPLATES["then-i-am"]
    call = asyncio.run(
        common.judge_with_backoff(caller, duel, [], "a rock", "I am rust, patient.", "")
    )
    assert call.response is not None and len(calls) == 3
    assert round(call.cost_usd, 4) == 0.004
    assert call.attempts[:2] == ["call_error: timeout", "call_error: timeout"]

    class Broken(ScriptedCaller):
        async def judge(self, *args, **kwargs):
            return JudgeCall(response=None, raw="{", prompt_hash="t", attempts=["unparseable"])

    call = asyncio.run(
        common.judge_with_backoff(Broken([], []), duel, [], "a rock", "I am rust.", "")
    )
    assert call.response is None and call.attempts == ["unparseable"]


def test_the_corpus_takes_class_examples_and_finished_train_variants_only(monkeypatch):
    spec = load_spec("counter")[0]
    cell = Cell(
        klass="counter",
        parent="then-i-am",
        theme="kitchens",
        voice=spec.axes.voice[0],
        content_rating="mild",
        weights=[5, 3, 2],
    )
    done = Entry(klass="counter", parent="then-i-am", cell=cell, stage="seeds", split="heldout")
    piloted = done.model_copy(update={"stage": "pilot", "split": "train"})
    monkeypatch.setattr(run, "load_index", lambda: {"kept": done, "early": piloted})
    with pytest.raises(SystemExit, match="held out"):
        run.corpus_games(["kept"])
    with pytest.raises(SystemExit, match="full pool"):
        run.corpus_games(["early"])
    assert run.corpus_games(["then-i-am"])[1] == {"then-i-am": "counter"}


def test_the_lane_total_counts_the_funnel_ledgers(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(run, "PILOTS_DIR", tmp_path / "pilots")
    pilot = Ledger(tmp_path / "pilots" / "build.db")
    pilot.add_call(
        CallRow("m", 0, "judge", "p1", 1, "j", "", "", None, {}, 0, 0, 1.25, 0, "parsed")
    )
    pilot.close()
    assert run.lane_total() == 1.25


def test_running_out_of_credit_stops_the_run_and_leaves_its_matches_to_resume(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(run, "RUNS_DIR", tmp_path)
    ledger = Ledger(tmp_path / "r.db")
    ledger.create_run(
        "r",
        {
            "matches": {"then-i-am": 2},
            "provider": "fireworks",
            "judge_ref": "judge-fireworks",
            "flash_ref": "opponent-fireworks",
            "classes": {"then-i-am": "counter"},
        },
    )
    run.plan_matches(ledger, TEMPLATES, {"then-i-am": 2}, random.Random(1), "opponent-fireworks")
    monkeypatch.setattr(run, "make_caller", lambda judge_ref: NoCredit([], []))
    monkeypatch.setattr(run, "credit_left", fake_credit)
    monkeypatch.setattr(run, "load_model", lambda ref: ModelSpec(model=ref, display_name=ref))
    asyncio.run(run.drive("r", ledger, TEMPLATES, budget=5, now=True, concurrency=1))
    assert len(ledger.matches("active")) == 2 and ledger.all_calls() == []
    assert ledger.conn.execute("select finished from runs").fetchone()["finished"] is None
