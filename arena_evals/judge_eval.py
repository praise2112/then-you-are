"""The judge SLM's held-out eval: Flash's verdicts on the bake-off answers at the eval contexts,
each as the judge SLM's conversation, and the D13 scores of an SLM's replies against them.

    uv run python -m arena_evals.judge_eval build
    uv run python -m arena_evals.judge_eval score ANSWERS.jsonl [--eval SAMPLE.jsonl]

`build` writes judge-eval.jsonl next to the contexts; each record has an id and messages, so
sft.py --judge --contexts judge-eval.jsonl answers it after training. `score` reads those
answers ({"context": id, "text": reply}).
"""

import argparse
import itertools
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import BaseModel

from arena_core.state import weighted_total
from arena_core.template import Template, load_template_file
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
    templates = {s: load_template_file(pool_path(k, s)) for k, s in heldout_variants()}
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
    EVAL_PATH.write_text("".join(v.model_dump_json() + "\n" for v in out))
    print(f"{len(out)} verdicts from {len(ROWS)} rows", file=sys.stderr)


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
    """D13's measures of the SLM's replies against Flash's verdicts. An unparseable reply
    counts as a disagreement; score error and ranking use replies that parsed."""
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
    sc = sub.add_parser("score")
    sc.add_argument("answers", type=Path)
    sc.add_argument("--eval", type=Path, default=EVAL_PATH, help="a sample of the eval set")
    args = ap.parse_args()
    if args.cmd == "build":
        build()
        return
    verdicts = [EvalVerdict.model_validate_json(x) for x in args.eval.read_text().splitlines()]
    templates = {s: load_template_file(pool_path(k, s)) for k, s in heldout_variants()}
    answers = {
        a["context"]: a["text"] for a in map(json.loads, args.answers.read_text().splitlines())
    }
    report = score(verdicts, answers, templates)
    print(report.model_dump_json(indent=1))
    passed = report.agreement >= 0.8 and report.kappa >= 0.6
    print(
        f"D13: {'passes' if passed else 'fails'} agreement and kappa; ranking "
        f"{'passes' if report.ranking_agreement >= 0.75 else 'fails'}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
