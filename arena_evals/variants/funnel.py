"""The funnel after rank: calibrate each class example, pilot the rank survivors against its
intervals, check judge consistency, grow the full seed pools. Stages land in pool/index.json.

uv run python -m arena_evals.variants.funnel calibrate then-i-am --matches 100 --budget 1
uv run python -m arena_evals.variants.funnel pilot counter --budget 3
uv run python -m arena_evals.variants.funnel consistency counter --budget 1
uv run python -m arena_evals.variants.funnel seeds counter --budget 1
"""

import argparse
import asyncio
import hashlib
import random
import statistics
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from arena_core.state import Actor, deal, normalize, weighted_total
from arena_core.template import (
    SCORE_MAX,
    Seed,
    Strict,
    Template,
    load_template,
    load_template_file,
)
from arena_evals.common import (
    credit_left,
    judge_with_backoff,
    load_model,
    make_caller,
)
from arena_evals.datagen.ledger import BudgetReached, CallFailed, Ledger, judge_call_row
from arena_evals.datagen.play import MatchAbandoned, Teacher, new_match, play_match
from arena_evals.datagen.sabotage import Position, positions
from arena_evals.grow_seeds import grow
from arena_evals.variants.generate import POOL_DIR, Stage, load_index, pool_path, save_index
from arena_evals.variants.spec import spec_names
from arena_judge.caller import CallError, ModelCaller, ModelSpec
from arena_judge.prompt import clean_move
from arena_judge.schema import JudgeResponse, route_outcome

PILOTS_DIR = POOL_DIR / "pilots"
CALIBRATION_PATH = POOL_DIR / "calibration.json"
PILOT_MATCHES = 10
PILOT_SEEDS = 20
FULL_SEEDS = 60
REJUDGE_SAME = 20
REJUDGE_LUNA = 10
BOOTSTRAP = 500
JUDGE = "judge-fireworks"
FLASH = "opponent-fireworks"
LUNA_JUDGE = "judge-luna"
MIN_PASS_RATE = 0.6
MAX_DUP_RATE = 0.1
MIN_LENGTH = 3


class Stats(Strict):
    matches: int
    judged: int
    refused: int
    pass_rate: float
    spread: float
    dup_rate: float
    median_length: float | None


class Calibration(Strict):
    """Bootstrapped 5th to 95th percentile of each pilot statistic for one class example."""

    matches: int
    pilot_size: int
    pass_rate: list[float]
    spread: list[float]
    dup_rate: list[float]
    median_length: list[float] | None
    agreement_same: list[float]
    agreement_luna: list[float]


@dataclass(frozen=True)
class Summary:
    """One match's teacher moves: judged outcomes, weighted totals of the moves that stood,
    the normalised move texts, and how many moves the rule check refused before any judge."""

    outcomes: list[str]
    totals: list[float]
    norms: list[str]
    refused: int


def summarize(template: Template, ledger: Ledger, match_ids: list[str]) -> list[Summary]:
    """The engine's default move is left out: it stands in for refused teacher moves."""
    found = []
    for match_id in match_ids:
        outcomes: list[str] = []
        totals: list[float] = []
        norms: list[str] = []
        refused = 0
        written: str | None = None
        for call in ledger.calls(match_id):
            if call.role == "move":
                refused += written is not None
                written = clean_move(call.raw)
                continue
            if call.payload["move"] == written:
                written = None
            if not call.payload["response"] or call.payload["move"] == template.default_move:
                continue
            response = JudgeResponse.model_validate(call.payload["response"])
            outcome = route_outcome(response.scoring)
            outcomes.append(outcome)
            norms.append(normalize(call.payload["move"]))
            if outcome != "semantic_reject":
                totals.append(weighted_total(response.scoring.scores, template.weights))
        found.append(Summary(outcomes, totals, norms, refused + (written is not None)))
    return found


def stats_of(template: Template, matches: list[Summary]) -> Stats:
    outcomes = [o for m in matches for o in m.outcomes]
    totals = [t for m in matches for t in m.totals]
    norms = [n for m in matches for n in m.norms]
    refused = sum(m.refused for m in matches)
    attempted = len(outcomes) + refused
    max_total = SCORE_MAX * sum(template.weights.values())
    return Stats(
        matches=len(matches),
        judged=len(outcomes),
        refused=refused,
        pass_rate=sum(o != "semantic_reject" for o in outcomes) / attempted if attempted else 0,
        spread=statistics.pstdev(totals) / max_total if len(totals) > 1 else 0,
        dup_rate=1 - len(set(norms)) / len(norms) if norms else 0,
        median_length=statistics.median(
            sum(o != "semantic_reject" for o in m.outcomes) for m in matches
        )
        if matches and template.win_condition == "sudden_death"
        else None,
    )


def match_stats(template: Template, ledger: Ledger, match_ids: list[str]) -> Stats:
    return stats_of(template, summarize(template, ledger, match_ids))


def percentiles(values: list[float]) -> list[float]:
    """The 5th and 95th percentiles."""
    cuts = statistics.quantiles(values, n=20)
    return [cuts[0], cuts[-1]]


def bootstrap(
    template: Template, ledger: Ledger, match_ids: list[str], size: int, rng: random.Random
) -> dict[str, list[float] | None]:
    """Intervals of each statistic over resampled pilots of `size` matches."""
    matches = summarize(template, ledger, match_ids)
    draws = [stats_of(template, rng.choices(matches, k=size)) for _ in range(BOOTSTRAP)]
    lengths = [d.median_length for d in draws if d.median_length is not None]
    return {
        "pass_rate": percentiles([d.pass_rate for d in draws]),
        "spread": percentiles([d.spread for d in draws]),
        "dup_rate": percentiles([d.dup_rate for d in draws]),
        "median_length": percentiles(lengths) if lengths else None,
    }


def agreement_interval(same: list[bool], rng: random.Random) -> list[float]:
    if not same:
        return [0.0, 0.0]
    return percentiles([statistics.mean(rng.choices(same, k=len(same))) for _ in range(BOOTSTRAP)])


def sanity_reasons(stats: Stats, cal: Calibration) -> list[str]:
    """Why a pilot fails the sanity filter; empty when it passes. A fixed bar gives way where
    the parent's own pilots fall outside it."""
    reasons = []
    if stats.pass_rate < MIN_PASS_RATE:
        reasons.append(f"pass rate {stats.pass_rate:.2f}")
    if stats.spread < cal.spread[0]:
        reasons.append(f"spread {stats.spread:.3f} under {cal.spread[0]:.3f}")
    if stats.dup_rate > max(MAX_DUP_RATE, cal.dup_rate[1]):
        reasons.append(f"duplicates {stats.dup_rate:.2f}")
    min_length = min(MIN_LENGTH, cal.median_length[0]) if cal.median_length else MIN_LENGTH
    if stats.median_length is not None and stats.median_length < min_length:
        reasons.append(f"median length {stats.median_length:.1f}")
    return reasons


def deal_for(template: Template, slug: str, i: int) -> list[Seed]:
    """The same cards for the same pilot match number, so a resumed run replays it."""
    return deal(template, random.Random(f"{slug}/{i}"))


async def play_pilot(
    template: Template,
    slug: str,
    n: int,
    ledger: Ledger,
    caller: ModelCaller,
    budget: float,
    spent: Callable[[], float],
) -> list[str]:
    """Plays the pilot matches not yet ended, Flash self-play judged by Flash, until `spent()`
    reaches `budget`. Returns every ended id of the pilot, played now or earlier."""
    flash = Teacher(FLASH, load_model(FLASH))
    teachers: dict[Actor, Teacher] = {"p1": flash, "p2": flash}
    judge = load_model(JUDGE)
    sem = asyncio.Semaphore(8)
    ids = [f"{slug}-{i}" for i in range(n)]
    done = {r["match_id"] for r in ledger.matches("ended")}

    failed: list[CallFailed] = []

    def over_budget() -> bool:
        return spent() >= budget or any(f.status == 402 for f in failed)

    async def one(i: int) -> None:
        match = new_match(template, deal_for(template, slug, i))
        match.id = ids[i]
        async with sem:
            if over_budget():
                return
            try:
                await play_match(template, match, teachers, caller, ledger, judge, over_budget)
            except MatchAbandoned as e:
                print(f"abandoned: {e}", file=sys.stderr)
            except BudgetReached:
                pass
            except CallFailed as e:
                failed.append(e)
                print(f"call failed: {e}", file=sys.stderr)

    await asyncio.gather(*(one(i) for i in range(n) if ids[i] not in done))
    if no_credit := next((f for f in failed if f.status == 402), None):
        raise no_credit
    done = {r["match_id"] for r in ledger.matches("ended")}
    return [m for m in ids if m in done]


async def rejudge(
    template: Template,
    ledger: Ledger,
    key: str,
    sample: list[Position],
    spec: ModelSpec,
    caller: ModelCaller,
) -> list[bool]:
    """Judges each position again with `spec`; True where the outcome matched the original.
    Rows are cached by position the moment they land, so a rerun pays only for new ones."""
    sem = asyncio.Semaphore(8)

    async def one(pos: Position) -> bool:
        pos_key = f"{key}/{position_id(pos)}"
        recorded = ledger.calls(pos_key)
        row = recorded[0] if recorded else None
        if row is None or row.payload["response"] is None:
            async with sem:
                call = await judge_with_backoff(
                    caller, template, pos.transcript, pos.previous, pos.move, pos.hidden, spec
                )
            if row is not None:
                call.cost_usd += row.cost_usd
            inputs = {
                "previous": pos.previous,
                "move": pos.move,
                "hidden": pos.hidden,
                "transcript": pos.transcript,
                "original": pos.outcome,
            }
            row = judge_call_row(pos_key, 0, pos.actor, pos.seq, spec, call, inputs)
            ledger.add_call(row)
        response = row.payload["response"]
        if response is None:
            status = row.payload.get("error_status")
            raise CallFailed(f"{pos_key}: judge gave no verdict ({status})", status)
        return route_outcome(JudgeResponse.model_validate(response).scoring) == pos.outcome

    return list(await asyncio.gather(*(one(pos) for pos in sample)))


def position_id(pos: Position) -> str:
    text = "\n".join([*pos.transcript, pos.previous, pos.move])
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def stood_positions(ledger: Ledger, match_ids: list[str]) -> list[Position]:
    found = []
    for match_id in match_ids:
        found.extend(positions(ledger.calls(match_id)))
    return found


async def agreement(
    template: Template,
    ledger: Ledger,
    slug: str,
    match_ids: list[str],
    caller: ModelCaller,
) -> tuple[list[bool], list[bool]]:
    """Re-judge agreement with the same judge and with Luna over a fixed sample of positions."""
    rng = random.Random(slug)
    stood = stood_positions(ledger, match_ids)
    rng.shuffle(stood)
    same, luna = await asyncio.gather(
        rejudge(
            template, ledger, f"{slug}/rejudge", stood[:REJUDGE_SAME], load_model(JUDGE), caller
        ),
        rejudge(
            template,
            ledger,
            f"{slug}/luna",
            stood[REJUDGE_SAME : REJUDGE_SAME + REJUDGE_LUNA],
            load_model(LUNA_JUDGE),
            caller,
        ),
    )
    return same, luna


CALIBRATION = TypeAdapter(dict[str, Calibration])


def load_calibration() -> dict[str, Calibration]:
    return (
        CALIBRATION.validate_json(CALIBRATION_PATH.read_bytes())
        if CALIBRATION_PATH.exists()
        else {}
    )


def save_calibration(cal: dict[str, Calibration]) -> None:
    CALIBRATION_PATH.write_bytes(CALIBRATION.dump_json(dict(sorted(cal.items())), indent=1) + b"\n")


async def calibrate(slug: str, matches: int, budget: float) -> Calibration:
    template = load_template(slug)
    ledger = Ledger(PILOTS_DIR / f"{slug}.db")
    caller = make_caller(judge_ref=JUDGE)
    try:
        ended = await play_pilot(template, slug, matches, ledger, caller, budget, ledger.spent)
        if len(ended) < matches or ledger.spent() >= budget:
            raise SystemExit(
                f"{slug}: {len(ended)} of {matches} matches ended for ${ledger.spent():.2f}; "
                "rerun with a larger --budget to finish and write the calibration"
            )
        same, luna = await agreement(template, ledger, slug, ended, caller)
    finally:
        await caller.aclose()
    rng = random.Random(0)
    intervals = bootstrap(template, ledger, ended, PILOT_MATCHES, rng)
    cal = Calibration(
        matches=len(ended),
        pilot_size=PILOT_MATCHES,
        agreement_same=agreement_interval(same, rng),
        agreement_luna=agreement_interval(luna, rng),
        **intervals,  # type: ignore[arg-type]
    )
    all_cal = load_calibration()
    all_cal[slug] = cal
    save_calibration(all_cal)
    print(f"{slug}: {len(ended)} matches, ${ledger.spent():.4f}", file=sys.stderr)
    print(cal.model_dump_json(indent=1))
    ledger.close()
    return cal


async def grow_pool(path: Path, total: int, budget: float) -> float:
    """Grows the file's pool up to `total` seeds; returns what it cost."""
    shortfall = total - len(load_template_file(path).seed_pool)
    if shortfall <= 0:
        return 0.0
    report = await grow(path, shortfall, 16, 0.65, dry_run=False, budget=budget)
    return report.cost_usd


async def for_each(
    klass: str,
    stage: Stage,
    budget: float,
    step: Callable[[str, Ledger, ModelCaller, float], Awaitable[float]],
    only: set[str] | None = None,
) -> None:
    """Runs `step` over every variant of the class that passed `stage`, or those of them in
    `only`, until the budget or the account's balance is spent or the credit runs out."""
    index = load_index()
    slugs = sorted(
        s
        for s, e in index.items()
        if e.klass == klass and e.stage == stage and e.reject is None and (not only or s in only)
    )
    if only and (unknown := only - set(slugs)):
        raise SystemExit(f"not {klass} variants at stage {stage}: {', '.join(sorted(unknown))}")
    if not slugs:
        raise SystemExit(f"no {klass} variant at stage {stage}")
    ledger = Ledger(PILOTS_DIR / f"{klass}.db")
    caller = make_caller(judge_ref=JUDGE)
    spent = 0.0
    try:
        budget = min(budget, await credit_left(caller))
        for slug in slugs:
            if spent >= budget:
                print(f"budget reached at ${spent:.2f}, stopping before {slug}", file=sys.stderr)
                break
            before = ledger.spent()
            try:
                spent += await step(slug, ledger, caller, budget - spent)
            except (CallFailed, CallError) as e:
                spent += ledger.spent() - before
                print(f"  {slug:32s} call failed, stage unchanged: {e}", file=sys.stderr)
                if e.status == 402:
                    print("OpenRouter is out of credit; stopping", file=sys.stderr)
                    break
    finally:
        await caller.aclose()
        ledger.close()
    print(f"{klass} {stage}: {len(slugs)} variants, ${spent:.4f}", file=sys.stderr)


async def pilot(klass: str, budget: float, only: set[str] | None = None) -> None:
    cal = load_calibration()

    async def step(slug: str, ledger: Ledger, caller: ModelCaller, left: float) -> float:
        entry = load_index()[slug]
        if entry.parent not in cal:
            raise SystemExit(f"{entry.parent} has no calibration; run calibrate first")
        path = pool_path(klass, slug)
        cost = await grow_pool(path, PILOT_SEEDS, left)
        template = load_template_file(path)
        if len(template.seed_pool) < PILOT_SEEDS:
            print(f"  {slug:32s} pool short, stage unchanged", file=sys.stderr)
            return cost
        before = ledger.spent()
        ended = await play_pilot(
            template,
            slug,
            PILOT_MATCHES,
            ledger,
            caller,
            left,
            lambda: ledger.spent() - before + cost,
        )
        cost += ledger.spent() - before
        if len(ended) < PILOT_MATCHES:
            print(f"  {slug:32s} {len(ended)} matches ended, stage unchanged", file=sys.stderr)
            return cost
        stats = match_stats(template, ledger, ended)
        reasons = sanity_reasons(stats, cal[entry.parent])
        entry.stats = stats.model_dump()
        entry.stage = "pilot"
        entry.reject = "; ".join(reasons) or None
        save_index({slug: entry})
        print(f"  {slug:32s} {entry.reject or 'passed'}", file=sys.stderr)
        return cost

    await for_each(klass, "rank", budget, step, only)


async def consistency(klass: str, budget: float) -> None:
    cal = load_calibration()

    async def step(slug: str, ledger: Ledger, caller: ModelCaller, left: float) -> float:
        entry = load_index()[slug]
        path = pool_path(klass, slug)
        template = load_template_file(path)
        ended = [f"{slug}-{i}" for i in range(PILOT_MATCHES) if ledger.calls(f"{slug}-{i}")]
        before = ledger.spent()
        same, luna = await agreement(template, ledger, slug, ended, caller)
        floor = cal[entry.parent]
        reasons = []
        if statistics.mean(same or [0.0]) < floor.agreement_same[0]:
            reasons.append(f"re-judge agreement {statistics.mean(same or [0.0]):.2f}")
        if statistics.mean(luna or [0.0]) < floor.agreement_luna[0]:
            reasons.append(f"Luna agreement {statistics.mean(luna or [0.0]):.2f}")
        entry.stage = "consistency"
        entry.reject = "; ".join(reasons) or None
        save_index({slug: entry})
        print(f"  {slug:32s} {entry.reject or 'passed'}", file=sys.stderr)
        return ledger.spent() - before

    await for_each(klass, "pilot", budget, step)


async def seeds(klass: str, budget: float) -> None:
    async def step(slug: str, ledger: Ledger, caller: ModelCaller, left: float) -> float:
        path = pool_path(klass, slug)
        cost = await grow_pool(path, FULL_SEEDS, left)
        if len(load_template_file(path).seed_pool) < FULL_SEEDS:
            print(f"  {slug:32s} pool short, stage unchanged", file=sys.stderr)
            return cost
        entry = load_index()[slug]
        entry.stage = "seeds"
        save_index({slug: entry})
        return cost

    await for_each(klass, "consistency", budget, step)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, required=True, help="dollars; the step stops here")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cal = sub.add_parser("calibrate")
    cal.add_argument("slug")
    cal.add_argument("--matches", type=int, default=100)
    for name in ("pilot", "consistency", "seeds"):
        sub.add_parser(name).add_argument("klass", choices=spec_names())
    sub.choices["pilot"].add_argument("--only", default="", help="comma-separated variant slugs")
    args = ap.parse_args()
    if args.cmd == "calibrate":
        asyncio.run(calibrate(args.slug, args.matches, args.budget))
    elif args.cmd == "pilot":
        asyncio.run(pilot(args.klass, args.budget, set(filter(None, args.only.split(",")))))
    elif args.cmd == "consistency":
        asyncio.run(consistency(args.klass, args.budget))
    else:
        asyncio.run(seeds(args.klass, args.budget))


if __name__ == "__main__":
    main()
