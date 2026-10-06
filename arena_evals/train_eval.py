"""Frozen eval contexts on held-out variants, and the scoring of any model's answers at them.

A context is a position where Flash wrote a move in Flash self-play, kept with the match as it
stood. Every model answers the same contexts and one judge scores each answer, so rows compare
move for move. Every call is recorded, so a rerun replays and pays only for what is new.

    python -m arena_evals.train_eval contexts --budget 0.5 --matches 75
    python -m arena_evals.train_eval answer luna opponent-luna6 --budget 0.4
    python -m arena_evals.train_eval score luna --budget 0.3 [--judge judge-sol --sample 100]
    python -m arena_evals.train_eval compare luna [--against flash --judge judge-fireworks]
"""

import argparse
import asyncio
import bisect
import dataclasses
import json
import math
import random
import statistics
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel

from arena_core.state import STANDING, Actor, Guess, Match, Turn, layer1, weighted_total
from arena_core.template import Template, load_template_file
from arena_evals.common import credit_left, load_model, make_caller
from arena_evals.datagen.ledger import (
    BudgetReached,
    CallFailed,
    JudgeInputs,
    Ledger,
    Tape,
    move_call_row,
)
from arena_evals.datagen.play import MatchPlayer, Teacher, new_match
from arena_evals.variants.funnel import FLASH, JUDGE, deal_for, play_pilot
from arena_evals.variants.generate import load_index, pool_path
from arena_judge.caller import ModelCaller
from arena_judge.prompt import clean_move
from arena_judge.schema import JudgeResponse, route_outcome

EVAL_DIR = Path(__file__).parent / "heldout"
CONTEXTS_PATH = EVAL_DIR / "contexts.jsonl"
ROWS_DIR = EVAL_DIR / "rows"
BOOTSTRAP = 1000
CONCURRENCY = 8


class Scored(BaseModel):
    """One answer's result: an engine outcome, or "refused" when the rule check stopped it.
    `total` is the weighted rubric total, 0 for a move that did not count."""

    text: str
    outcome: str
    total: float

    @property
    def stood(self) -> bool:
        return self.outcome in STANDING


class Context(BaseModel):
    id: str
    template_id: str
    game_class: str
    match_id: str
    actor: Actor
    seq: int
    messages: list[dict[str, str]]
    match: dict
    previous: str
    hidden: str
    lines: list[str]
    flash: Scored


def verdict_score(template: Template, text: str, response: JudgeResponse) -> Scored:
    outcome = route_outcome(response.scoring)
    counted = outcome != "semantic_reject"
    total = weighted_total(response.scoring.scores, template.weights) if counted else 0
    return Scored(text=text, outcome=outcome, total=total)


def match_of(context: Context) -> Match:
    d = context.match
    return Match(
        **{
            **d,
            "seats": tuple(d["seats"]),
            "guessers": tuple(d["guessers"]),
            "turns": [Turn(**t) for t in d["turns"]],
            "guesses": [Guess(**g) for g in d["guesses"]],
        }
    )


class ContextRecorder(MatchPlayer):
    """Replays a recorded match and keeps the first position each written move was judged at."""

    def __init__(self, *args, game_class: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.game_class = game_class
        self.contexts: dict[str, Context] = {}

    async def _judge(self, actor: Actor, ask: JudgeInputs) -> JudgeResponse:
        before = dataclasses.asdict(self.match)
        response = await super()._judge(actor, ask)
        written = next(r for r in reversed(self.tape.recorded[: self.tape.idx]) if r.role == "move")
        key = f"{self.match.id}/{written.seq}/{actor}"
        if (
            ask.move != self.template.default_move
            and clean_move(written.raw) == ask.move
            and key not in self.contexts
        ):
            self.contexts[key] = Context(
                id=key,
                template_id=self.template.slug,
                game_class=self.game_class,
                match_id=self.match.id,
                actor=actor,
                seq=written.seq,
                messages=written.payload["messages"],
                match=before,
                previous=ask.previous,
                hidden=ask.hidden,
                lines=ask.transcript,
                flash=verdict_score(self.template, ask.move, response),
            )
        return response


def heldout_variants() -> list[tuple[str, str]]:
    """(class, slug) of every held-out variant whose seed pool is complete."""
    return sorted(
        (e.klass, slug)
        for slug, e in load_index().items()
        if e.split == "heldout" and e.stage == "seeds" and e.reject is None
    )


async def record_contexts(
    games: list[tuple[str, str, Template]], per_variant: int, ledger: Ledger, caller: ModelCaller
) -> list[Context]:
    """Every context of the ended matches in the ledger. Replays only; never calls a model."""
    flash = Teacher(FLASH, load_model(FLASH))
    judge = load_model(JUDGE)
    ended = {r["match_id"] for r in ledger.matches("ended")}
    contexts: list[Context] = []
    for klass, slug, template in games:
        for i in range(per_variant):
            if f"{slug}-{i}" not in ended:
                continue
            match = new_match(template, deal_for(template, slug, i))
            match.id = f"{slug}-{i}"
            teachers: dict[Actor, Teacher] = {"p1": flash, "p2": flash}
            recorder = ContextRecorder(
                template, match, teachers, caller, ledger, judge, lambda: True, game_class=klass
            )
            await recorder.play()
            contexts.extend(recorder.contexts.values())
    return contexts


async def build_contexts(matches: int, budget: float) -> None:
    variants = heldout_variants()
    if not variants:
        raise SystemExit("no held-out variant has finished its seed pool yet")
    games = [(k, s, load_template_file(pool_path(k, s))) for k, s in variants]
    per_variant = math.ceil(matches / len(games))
    EVAL_DIR.mkdir(exist_ok=True)
    ledger = Ledger(EVAL_DIR / "contexts.db")
    caller = make_caller(judge_ref=JUDGE)
    start = ledger.spent()
    try:
        budget = min(budget, await credit_left(caller))
        for _, slug, template in games:
            await play_pilot(
                template, slug, per_variant, ledger, caller, budget, lambda: ledger.spent() - start
            )
        contexts = await record_contexts(games, per_variant, ledger, caller)
        spent = ledger.spent() - start
    finally:
        await caller.aclose()
        ledger.close()
    CONTEXTS_PATH.write_text("".join(c.model_dump_json() + "\n" for c in contexts))
    print(
        f"{len(contexts)} contexts from {len(games)} held-out variants, ${spent:.4f} spent now",
        file=sys.stderr,
    )


def load_contexts() -> list[Context]:
    if not CONTEXTS_PATH.exists():
        raise SystemExit(f"{CONTEXTS_PATH} is missing; run the contexts step first")
    return [Context.model_validate_json(line) for line in CONTEXTS_PATH.read_text().splitlines()]


def sample(contexts: list[Context], n: int) -> list[Context]:
    """About `n` contexts, each class in proportion to its share, the same ones every run."""
    rng = random.Random(0)
    picked = []
    for klass in sorted({c.game_class for c in contexts}):
        pool = sorted((c for c in contexts if c.game_class == klass), key=lambda c: c.id)
        picked += rng.sample(pool, min(len(pool), round(n * len(pool) / len(contexts))))
    return picked


def answers_path(row: str) -> Path:
    return ROWS_DIR / f"{row}.answers.jsonl"


def scored_path(row: str, judge_ref: str) -> Path:
    return ROWS_DIR / f"{row}.{judge_ref}.jsonl"


def load_answers(row: str, contexts: list[Context]) -> dict[str, str]:
    """Answers by context id; the flash row is Flash's own moves."""
    if row == "flash":
        return {c.id: c.flash.text for c in contexts}
    path = answers_path(row)
    if not path.exists():
        raise SystemExit(f"{path} is missing")
    return {a["context"]: a["text"] for a in map(json.loads, path.read_text().splitlines())}


async def run_calls(
    row: str,
    budget: float,
    call: Callable[[Context, Ledger, ModelCaller, Callable[[], bool]], Awaitable[dict]],
    contexts: list[Context],
) -> list:
    """Runs `call` over the contexts under one ledger and budget; failed contexts are left out
    so a rerun asks again. A 402 stops the step."""
    ROWS_DIR.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(ROWS_DIR / f"{row}.db")
    caller = make_caller(judge_ref=JUDGE)
    start = ledger.spent()
    sem = asyncio.Semaphore(CONCURRENCY)
    failed: list[CallFailed] = []
    try:
        budget = min(budget, await credit_left(caller))

        def over_budget() -> bool:
            return ledger.spent() - start >= budget or any(f.status == 402 for f in failed)

        async def one(c: Context):
            async with sem:
                try:
                    return await call(c, ledger, caller, over_budget)
                except BudgetReached:
                    return None
                except CallFailed as e:
                    failed.append(e)
                    return None

        results = await asyncio.gather(*(one(c) for c in contexts))
        spent = ledger.spent() - start
    finally:
        await caller.aclose()
        ledger.close()
    done = [r for r in results if r is not None]
    print(
        f"{row}: {len(done)} of {len(contexts)} done, ${spent:.4f} spent now",
        file=sys.stderr,
    )
    if no_credit := next((f for f in failed if f.status == 402), None):
        raise SystemExit(f"OpenRouter is out of credit: {no_credit}")
    return done


async def answer(row: str, ref: str, budget: float) -> None:
    """An API model's answer at every context, written to the row's answers file."""
    spec = load_model(ref)

    async def one(c: Context, ledger: Ledger, caller: ModelCaller, over_budget) -> dict:
        tape = Tape(ledger, f"{c.id}/answer", over_budget)
        inputs = {"messages": c.messages}
        written = await tape.step(
            "move",
            c.actor,
            c.seq,
            lambda idx: move_call_row(
                caller, spec, c.messages, tape.key, idx, c.actor, c.seq, ref, inputs
            ),
            inputs,
        )
        return {"context": c.id, "text": written.raw}

    done = await run_calls(row, budget, one, load_contexts())
    answers_path(row).write_text("".join(json.dumps(a) + "\n" for a in done))


async def score(row: str, judge_ref: str, budget: float, n: int | None) -> None:
    """The rule check, then the judge, for each of the row's answers."""
    contexts = load_contexts()
    answers = load_answers(row, contexts)
    todo = [c for c in (sample(contexts, n) if n else contexts) if c.id in answers]
    templates = {
        c.template_id: load_template_file(pool_path(c.game_class, c.template_id)) for c in todo
    }
    spec = load_model(judge_ref)

    async def one(c: Context, ledger: Ledger, caller: ModelCaller, over_budget) -> dict:
        template = templates[c.template_id]
        text = clean_move(answers[c.id])
        if layer1(template, text, match_of(c)) is not None:
            return {"context": c.id, **Scored(text=text, outcome="refused", total=0).model_dump()}
        tape = Tape(ledger, f"{c.id}/{judge_ref}", over_budget)
        ask = JudgeInputs(c.previous, text, c.hidden, c.lines)
        response = await tape.judge(caller, spec, template, c.actor, c.seq, ask)
        return {"context": c.id, **verdict_score(template, text, response).model_dump()}

    done = await run_calls(row, budget, one, todo)
    scored_path(row, judge_ref).write_text("".join(json.dumps(s) + "\n" for s in done))


def load_scored(row: str, judge_ref: str, contexts: list[Context]) -> dict[str, Scored]:
    if row == "flash" and judge_ref == JUDGE:
        return {c.id: c.flash for c in contexts}
    path = scored_path(row, judge_ref)
    if not path.exists():
        raise SystemExit(f"{path} is missing; run the score step first")
    return {s["context"]: Scored(**s) for s in map(json.loads, path.read_text().splitlines())}


def interval(draws: list[float]) -> tuple[float, float]:
    """The central 95%."""
    cuts = statistics.quantiles(draws, n=40)
    return cuts[0], cuts[-1]


def paired_difference(
    row: dict[str, Scored], base: dict[str, Scored], match_of_id: dict[str, str], rng: random.Random
) -> tuple[float, float, float]:
    """Stood rate of `row` minus `base` on the contexts both answered, with a bootstrap 95%
    interval that resamples whole matches."""
    by_match: dict[str, list[str]] = {}
    for cid in sorted(row.keys() & base.keys()):
        by_match.setdefault(match_of_id[cid], []).append(cid)
    matches = sorted(by_match)

    def diff(picked: list[str]) -> float:
        ids = [cid for m in picked for cid in by_match[m]]
        return statistics.mean(row[i].stood - base[i].stood for i in ids)

    draws = [diff(rng.choices(matches, k=len(matches))) for _ in range(BOOTSTRAP)]
    return (diff(matches), *interval(draws))


def length_matched(row: dict[str, Scored], base: dict[str, Scored]) -> float:
    """Mean total of `row` minus `base` within quartiles of `base`'s move lengths, weighted by
    how many of `row`'s answers fall in each quartile."""
    ids = sorted(row.keys() & base.keys())
    cuts = statistics.quantiles([len(base[i].text) for i in ids], n=4)
    rows: dict[int, list[float]] = {}
    bases: dict[int, list[float]] = {}
    for i in ids:
        rows.setdefault(bisect.bisect(cuts, len(row[i].text)), []).append(row[i].total)
        bases.setdefault(bisect.bisect(cuts, len(base[i].text)), []).append(base[i].total)
    kept = [(rows[q], bases[q]) for q in rows.keys() & bases.keys()]
    weight = sum(len(r) for r, _ in kept)
    return sum(len(r) * (statistics.mean(r) - statistics.mean(b)) for r, b in kept) / weight


def compare(row: str, against: str, judge_ref: str) -> None:
    contexts = load_contexts()
    a, b = load_scored(row, judge_ref, contexts), load_scored(against, judge_ref, contexts)
    ids = sorted(a.keys() & b.keys())
    if len(ids) < 2:
        raise SystemExit(f"{row} and {against} share {len(ids)} scored contexts")
    match_of_id = {c.id: c.match_id for c in contexts}
    point, lo, hi = paired_difference(a, b, match_of_id, random.Random(0))
    print(f"{row} against {against}, judged by {judge_ref}, {len(ids)} contexts")
    for name, s in ((row, a), (against, b)):
        answers = [s[i] for i in ids]
        print(
            f"  {name:24s} stood {statistics.mean(x.stood for x in answers):6.1%}  "
            f"total {statistics.mean(x.total for x in answers):5.1f}  "
            f"refused {statistics.mean(x.outcome == 'refused' for x in answers):5.1%}  "
            f"rejected {statistics.mean(x.outcome == 'semantic_reject' for x in answers):5.1%}  "
            f"median length {statistics.median(len(x.text) for x in answers):.0f}"
        )
    print(
        f"  stood difference {point * 100:+.1f} points, 95% [{lo * 100:+.1f}, {hi * 100:+.1f}], "
        f"half-width {(hi - lo) * 50:.1f}"
    )
    print(f"  length-matched total difference {length_matched(a, b):+.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ctx = sub.add_parser("contexts")
    ctx.add_argument("--matches", type=int, default=75)
    ans = sub.add_parser("answer")
    ans.add_argument("row")
    ans.add_argument("ref", help="a models.yaml entry")
    sc = sub.add_parser("score")
    sc.add_argument("row")
    sc.add_argument("--judge", default=JUDGE)
    sc.add_argument("--sample", type=int, help="score only this many contexts, stratified")
    for p in (ctx, ans, sc):
        p.add_argument("--budget", type=float, required=True, help="dollars; the step stops here")
    cmp = sub.add_parser("compare")
    cmp.add_argument("row")
    cmp.add_argument("--against", default="flash")
    cmp.add_argument("--judge", default=JUDGE)
    args = ap.parse_args()
    if args.cmd == "contexts":
        asyncio.run(build_contexts(args.matches, args.budget))
    elif args.cmd == "answer":
        asyncio.run(answer(args.row, args.ref, args.budget))
    elif args.cmd == "score":
        asyncio.run(score(args.row, args.judge, args.budget, args.sample))
    else:
        compare(args.row, args.against, args.judge)


if __name__ == "__main__":
    main()
