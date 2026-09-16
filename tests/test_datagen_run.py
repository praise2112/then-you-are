import asyncio
import random
from datetime import UTC, datetime
from pathlib import Path

from arena_core.template import load_template
from arena_evals.datagen import card, run
from arena_evals.datagen.ledger import Ledger
from arena_judge.caller import ModelSpec
from tests.conftest import judge_response
from tests.test_datagen_play import ScriptedCaller

TEMPLATES = {"then-i-am": load_template("then-i-am")}


def test_the_window_guard_knows_the_off_peak_hours():
    assert run.in_window(datetime(2026, 9, 16, 17, 0, tzinfo=UTC))
    assert run.in_window(datetime(2026, 9, 16, 0, 10, tzinfo=UTC))
    assert not run.in_window(datetime(2026, 9, 16, 12, 0, tzinfo=UTC))
    wait = run.seconds_until_open(datetime(2026, 9, 16, 12, 0, tzinfo=UTC))
    assert wait == 4.5 * 3600


def test_the_estimate_prices_peak_above_off_peak():
    off_peak, peak = run.estimate(TEMPLATES, {"then-i-am": 100})
    assert 0.5 < off_peak < 2 and 2 < peak / off_peak < 2.7


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

    class NoSabotage:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self) -> int:
            return 0

    monkeypatch.setattr(run, "Saboteur", NoSabotage)
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

    text = card.write("r", ledger, tmp_path / "corpus", {"then-i-am": "counter"})
    assert "then-i-am: 3 ended" in text
    assert "player.jsonl: 3" in text and "Spearman" in text
    assert (tmp_path / "corpus" / "player.jsonl").exists()
