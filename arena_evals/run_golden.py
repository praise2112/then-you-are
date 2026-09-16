"""Golden set runner: sends fixed move pairs to the live judge and reports drift.

uv run python -m arena_evals.run_golden --template then-i-am --split dev [--judge judge-luna]
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from arena_core.template import Template, load_template
from arena_evals.common import make_caller
from arena_judge.caller import ModelCaller
from arena_judge.schema import Outcome, route_outcome

GOLDEN_DIR = Path(__file__).parent / "golden"
Split = Literal["dev", "holdout"]

STATUS_WORDS = r"accepted|rejected|refused|denied|approved|verdict"
DASH = re.compile(r"—|–|\s-\s|\.\.\.")


class GoldenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    template_id: str
    category: Literal["boundary", "close_creative", "adversarial"]
    previous_move: str
    move: str
    hidden: str = ""
    transcript: list[str] = []
    expected: Literal["accept", "fail", "semantic_reject"]
    notes: str
    provenance: Literal["live_match", "authored"]


def load_golden(template_id: str, split: Split) -> list[GoldenRecord]:
    lines = (GOLDEN_DIR / template_id / "v1" / f"{split}.jsonl").read_text().splitlines()
    records = [GoldenRecord.model_validate_json(line) for line in lines if line.strip()]
    ids = [r.id for r in records]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate golden ids")
    if {r.template_id for r in records} != {template_id}:
        raise ValueError(f"records in {template_id}/{split} name another template")
    return records


def status_opener(persona_name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^\s*(({STATUS_WORDS})\s*[:,.!]|move refused|{re.escape(persona_name)} refuses)",
        re.IGNORECASE,
    )


def headline_faults(headline: str, persona_name: str = "The Ringmaster") -> list[str]:
    faults = []
    if status_opener(persona_name).search(headline):
        faults.append("status opener")
    if DASH.search(headline):
        faults.append("dash")
    if len(headline) > 140:
        faults.append("over 140")
    return faults


class Result(BaseModel):
    id: str
    expected: str
    got: Outcome | None
    confidence: str | None
    scores: dict[str, int]
    headline: str
    faults: list[str]
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    attempts: list[str]

    @property
    def drifted(self) -> bool:
        return self.got != self.expected


async def judge_one(
    caller: ModelCaller, template: Template, record: GoldenRecord, sem: asyncio.Semaphore
) -> Result:
    transcript = record.transcript
    if (
        not transcript
        and template.mode == "escalation"
        and template.seed_named(record.previous_move) is None
    ):
        transcript = [f"player2: {record.previous_move}"]
    async with sem:
        call = await caller.judge(
            template, transcript, record.previous_move, record.move, record.hidden
        )
    meta: dict[str, Any] = dict(
        latency_ms=call.latency_ms,
        tokens_in=call.tokens_in,
        tokens_out=call.tokens_out,
        cost_usd=call.cost_usd,
        attempts=call.attempts,
    )
    if call.response is None:
        return Result(
            id=record.id,
            expected=record.expected,
            got=None,
            confidence=None,
            scores={},
            headline="",
            faults=["no ruling: " + ", ".join(call.attempts)],
            **meta,
        )
    scoring, host = call.response.scoring, call.response.host
    return Result(
        id=record.id,
        expected=record.expected,
        got=route_outcome(scoring),
        confidence=scoring.confidence,
        scores=scoring.scores,
        headline=host.headline,
        faults=headline_faults(host.headline, template.host.persona_name),
        **meta,
    )


async def run(
    template_id: str, split: Split, repeats: int, concurrency: int, only: set[str], judge: str
) -> list[Result]:
    caller = make_caller(judge_ref=judge)
    sem = asyncio.Semaphore(concurrency)
    template = load_template(template_id)
    records = [r for r in load_golden(template_id, split) if not only or r.id in only]
    try:
        return await asyncio.gather(
            *(judge_one(caller, template, r, sem) for r in records for _ in range(repeats))
        )
    finally:
        await caller.aclose()


def report(results: list[Result]) -> bool:
    width = max(len(r.id) for r in results)
    for r in results:
        mark = "  " if not r.drifted else "!!"
        scores = " ".join(str(v) for v in r.scores.values())
        faults = f"  [{', '.join(r.faults)}]" if r.faults else ""
        cells = [mark, f"{r.id:<{width}}", f"{r.expected:<15}", f"{r.got or 'none':<18}"]
        cells += [f"{r.confidence or '':<9}", f"{scores:<6}", r.headline + faults]
        print(" ".join(cells))
    drift = [r for r in results if r.drifted]
    faulty = [r for r in results if r.faults]
    by_outcome = Counter((r.expected, r.got) for r in drift)
    print(f"\n{len(results)} rulings, {len(drift)} drifted, {len(faulty)} headline faults")
    for (expected, got), n in sorted(by_outcome.items()):
        print(f"  expected {expected}, got {got}: {n}")
    latencies = sorted(r.latency_ms for r in results)
    p95 = latencies[int(0.95 * (len(latencies) - 1))]
    first_try = sum(r.attempts[:1] == ["parsed"] for r in results)
    salvaged = sum("salvaged" in r.attempts for r in results)
    print(
        f"latency median {statistics.median(latencies):.0f} ms, p95 {p95} ms; "
        f"tokens out mean {statistics.mean(r.tokens_out for r in results):.0f}; "
        f"cost total ${sum(r.cost_usd for r in results):.4f}; "
        f"parsed first try {first_try}/{len(results)}, salvaged {salvaged}"
    )
    print("confidence:", dict(Counter(r.confidence or "none" for r in results)))
    return not drift and not faulty


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", default="then-i-am")
    parser.add_argument("--split", choices=["dev", "holdout"], default="dev")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", type=Path, help="write the results as JSON here")
    parser.add_argument("--only", default="", help="comma-separated record ids")
    parser.add_argument("--judge", default="judge-v1", help="a models.yaml entry")
    args = parser.parse_args()
    only = {i for i in args.only.split(",") if i}
    results = asyncio.run(
        run(args.template, args.split, args.repeats, args.concurrency, only, args.judge)
    )
    if args.out:
        args.out.write_text(
            json.dumps([r.model_dump() for r in results], indent=1, ensure_ascii=False)
        )
    sys.exit(0 if report(results) else 1)


if __name__ == "__main__":
    main()
