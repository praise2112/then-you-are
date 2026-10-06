"""The judge SLM's held-out eval: Flash's verdicts on the bake-off answers at the eval contexts,
each as the judge SLM's conversation, and the agreement, kappa and ranking of an SLM's replies
against them.

    uv run python -m arena_evals.judge_eval build
    uv run python -m arena_evals.judge_eval answer URL ANSWERS.jsonl [--eval SAMPLE.jsonl]
    uv run python -m arena_evals.judge_eval score ANSWERS.jsonl [--eval SAMPLE.jsonl]

`build` writes judge-eval.jsonl next to the contexts; each record has an id and messages, so
sft.py --judge --contexts judge-eval.jsonl answers it after training, or `answer` sends it to
an OpenAI-compatible server. `score` reads those answers ({"context": id, "text": reply}).
"""

import argparse
import asyncio
import itertools
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import httpx
from pydantic import BaseModel

from arena_core.state import weighted_total
from arena_core.template import Template, load_template_file
from arena_evals.common import read_jsonl, with_backoff, write_jsonl
from arena_evals.datagen.ledger import Ledger
from arena_evals.datagen.records import judged_before
from arena_evals.train_eval import EVAL_DIR, ROWS_DIR, heldout_variants, load_contexts
from arena_evals.variants.generate import pool_path
from arena_judge.caller import parse_judge
from arena_judge.prompt import render_judge_messages
from arena_judge.schema import JudgeResponse, route_outcome

EVAL_PATH = EVAL_DIR / "judge-eval.jsonl"
ROWS = (
    "flash-first",
    "qwen35-2b",
    "qwen3-17b",
    "lfm25-12b",
    "minicpm5-1b",
    "qwen35-08b",
    "qwen3-06b",
)
TEACHER_JUDGE = "judge-v1"
# A pair counts for ranking only when Flash's totals differ by twice its rescoring spread.
RANK_GAP = 7.5
STOOD = ("accept", "semantic_uncertain")
RULING_FIRST = ("gates", "confidence", "verdict", "truth_proximity", "scores", "evidence")


class EvalVerdict(BaseModel):
    id: str
    template_id: str
    context: str
    row: str
    messages: list[dict[str, str]]
    response: dict


class Report(BaseModel):
    verdicts: int
    agreement: float
    kappa: float
    unparsed: float
    score_error: float
    ranking_pairs: int
    ranking_agreement: float


def build() -> None:
    templates = heldout_templates()
    matches = Ledger(EVAL_DIR / "contexts.db")
    rows = {row: Ledger(ROWS_DIR / f"{row}.db") for row in ROWS}
    out = []
    for c in load_contexts():
        earlier = judged_before(matches.calls(c.match_id), (c.seq, -1))
        for row, ledger in rows.items():
            calls = [r for r in ledger.calls(f"{c.id}/{TEACHER_JUDGE}") if r.payload["response"]]
            if not calls:
                continue
            move = calls[-1].payload["move"]
            messages = render_judge_messages(
                templates[c.template_id], earlier, c.actor, c.previous, move, c.hidden
            )
            out.append(
                EvalVerdict(
                    id=f"{c.id}/{row}",
                    template_id=c.template_id,
                    context=c.id,
                    row=row,
                    messages=messages,
                    response=calls[-1].payload["response"],
                )
            )
    for ledger in [matches, *rows.values()]:
        ledger.close()
    write_jsonl(EVAL_PATH, out)
    print(f"{len(out)} verdicts from {len(ROWS)} rows", file=sys.stderr)


def taught_order(messages: list[dict[str, str]], ruling_first: bool) -> list[dict[str, str]]:
    """Earlier verdicts in the key order the judge was trained on, as sft.py orders them."""
    if not ruling_first:
        return messages
    return [
        {**m, "content": ruling_first_text(json.loads(m["content"]))}
        if m["role"] == "assistant"
        else m
        for m in messages
    ]


def ruling_first_text(verdict: dict) -> str:
    scoring = verdict["scoring"]
    verdict = {**verdict, "scoring": {k: scoring[k] for k in RULING_FIRST if k in scoring}}
    return json.dumps(verdict, ensure_ascii=False)


def heldout_templates() -> dict[str, Template]:
    return {s: load_template_file(pool_path(k, s)) for k, s in heldout_variants()}


async def answer(url: str, verdicts: list[EvalVerdict], ruling_first: bool) -> list[dict]:
    """The server's reply to each eval record at judge-v1's temperature."""
    sem = asyncio.Semaphore(32)
    async with httpx.AsyncClient(timeout=600) as client:

        async def one(v: EvalVerdict) -> dict:
            body = {
                "model": "m",
                "messages": taught_order(v.messages, ruling_first),
                "temperature": 0.5,
                "max_tokens": 640,
                "chat_template_kwargs": {"enable_thinking": False},
            }

            async def post() -> httpx.Response:
                resp = await client.post(f"{url.rstrip('/')}/chat/completions", json=body)
                resp.raise_for_status()
                return resp

            async with sem:
                resp = await with_backoff(post)
            return {"context": v.id, "text": resp.json()["choices"][0]["message"]["content"] or ""}

        return list(await asyncio.gather(*(one(v) for v in verdicts)))


def kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's kappa between the two raters' labels."""
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(first[k] * second[k] for k in first) / n**2
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def score(
    verdicts: list[EvalVerdict], answers: dict[str, str], templates: dict[str, Template]
) -> Report:
    """Agreement, kappa, score error and ranking of the SLM's replies against Flash's verdicts.
    An unparseable reply counts as a disagreement; score error and ranking use parsed replies."""
    labels: list[tuple[str, str]] = []
    errors: list[float] = []
    totals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    unparsed = 0
    for v in verdicts:
        template = templates[v.template_id]
        flash = JudgeResponse.model_validate(v.response)
        mine = parse_judge(answers.get(v.id, ""), list(template.weights))
        flash_outcome = route_outcome(flash.scoring)
        if mine is None:
            unparsed += 1
            labels.append((flash_outcome, "unparsed"))
            continue
        labels.append((flash_outcome, route_outcome(mine.scoring)))
        errors += [abs(mine.scoring.scores[k] - s) for k, s in flash.scoring.scores.items()]
        if flash_outcome in STOOD:
            totals[v.context].append(
                (
                    weighted_total(flash.scoring.scores, template.weights),
                    weighted_total(mine.scoring.scores, template.weights),
                )
            )
    ranked = []
    for pairs in totals.values():
        for (fa, ma), (fb, mb) in itertools.combinations(pairs, 2):
            if abs(fa - fb) > RANK_GAP:
                same = (ma - mb) * (fa - fb)
                ranked.append(1.0 if same > 0 else 0.5 if same == 0 else 0.0)
    return Report(
        verdicts=len(verdicts),
        agreement=sum(a == b for a, b in labels) / len(labels),
        kappa=kappa(labels),
        unparsed=unparsed / len(verdicts),
        score_error=statistics.mean(errors) if errors else 0.0,
        ranking_pairs=len(ranked),
        ranking_agreement=statistics.mean(ranked) if ranked else 0.0,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    an = sub.add_parser("answer")
    an.add_argument("url", help="an OpenAI-compatible base URL serving the judge as model m")
    an.add_argument("answers", type=Path)
    an.add_argument("--evidence-first", action="store_true")
    sc = sub.add_parser("score")
    sc.add_argument("answers", type=Path)
    for p in (an, sc):
        p.add_argument("--eval", type=Path, default=EVAL_PATH, help="a sample of the eval set")
    args = ap.parse_args()
    if args.cmd == "build":
        build()
        return
    verdicts = [EvalVerdict.model_validate(r) for r in read_jsonl(args.eval)]
    if args.cmd == "answer":
        done = asyncio.run(answer(args.url, verdicts, not args.evidence_first))
        write_jsonl(args.answers, done)
        return
    templates = heldout_templates()
    answers = {a["context"]: a["text"] for a in read_jsonl(args.answers)}
    report = score(verdicts, answers, templates)
    print(report.model_dump_json(indent=1))
    passed = report.agreement >= 0.8 and report.kappa >= 0.6
    print(
        f"bars: {'passes' if passed else 'fails'} agreement and kappa; ranking "
        f"{'passes' if report.ranking_agreement >= 0.75 else 'fails'}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
