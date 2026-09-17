"""Plan, drive, resume and report a data generation run.

uv run python -m arena_evals.datagen.run start <run_id> --matches then-i-am=40 --budget 2
uv run python -m arena_evals.datagen.run resume <run_id> --budget 2 --concurrency 16
uv run python -m arena_evals.datagen.run report <run_id>
"""

import argparse
import asyncio
import hashlib
import json
import random
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from arena_core.state import Actor, Match, deal
from arena_core.template import Template, load_template
from arena_evals.common import in_window, load_model, make_caller, seconds_until_open
from arena_evals.datagen import card
from arena_evals.datagen.ledger import Ledger
from arena_evals.datagen.play import MatchAbandoned, Teacher, new_match, play_match
from arena_evals.datagen.sabotage import RATE, Saboteur
from arena_evals.variants.spec import load_spec, spec_names
from arena_judge.caller import ModelCaller
from arena_judge.prompt import judge_prompt_hash

RUNS_DIR = Path(__file__).parent / "runs"
CORPUS_DIR = Path(__file__).parent / "corpus"
CARDS_DIR = Path(__file__).parent / "cards"
LANE_CEILING = 50.0


def classes() -> dict[str, str]:
    """Template slug to class name, from the class specs."""
    return {slug: name for name in spec_names() for slug in load_spec(name)[0].examples}


PROVIDERS = {
    "fireworks": {"judge": "judge-fireworks", "flash": "opponent-fireworks", "concurrency": 20},
    "deepseek": {"judge": "judge-v1", "flash": "opponent-v1", "concurrency": 64},
}
FLASH_SHARE = 0.8
LUNA = "opponent-luna"
PEAK_RATIO = 2.7
UNIT_USD = {"judge": 0.00067, "flash": 0.0001, "luna": 0.0003}


def estimate(templates: dict[str, Template], counts: dict[str, int]) -> tuple[float, float]:
    """Expected bill for the planned matches at the flat or off-peak Flash price, and at peak."""
    base = 0.0
    peak = 0.0
    for slug, n in counts.items():
        calls = templates[slug].move_budget * (1 + RATE)
        judge = calls * UNIT_USD["judge"]
        flash_moves = FLASH_SHARE * UNIT_USD["flash"] * calls
        luna_moves = (1 - FLASH_SHARE) * UNIT_USD["luna"] * calls
        base += n * (judge + flash_moves + luna_moves)
        peak += n * (PEAK_RATIO * (judge + flash_moves) + luna_moves)
    return base, peak


def lane_total() -> float:
    total = 0.0
    for path in RUNS_DIR.glob("*.db"):
        ledger = Ledger(path)
        total += ledger.spent()
        ledger.close()
    return total


def plan_matches(
    ledger: Ledger,
    templates: dict[str, Template],
    counts: dict[str, int],
    rng: random.Random,
    flash_ref: str,
) -> None:
    refs = [flash_ref, LUNA]
    weights = [FLASH_SHARE, 1 - FLASH_SHARE]
    for slug, n in counts.items():
        template = templates[slug]
        for _ in range(n):
            match = new_match(template, deal(template, rng))
            p1, p2 = rng.choices(refs, weights, k=2)
            ledger.add_match(match.id, slug, match.cards, p1, p2)


def match_from_row(template: Template, row) -> Match:
    cards = [template.seed_named(token) for token in json.loads(row["cards"])]
    match = new_match(template, [c for c in cards if c is not None])
    match.id = row["match_id"]
    return match


async def drive(
    run_id: str,
    ledger: Ledger,
    templates: dict[str, Template],
    budget: float,
    now: bool,
    concurrency: int,
) -> None:
    plan = ledger.plan(run_id)
    assert plan is not None
    caller = make_caller(judge_ref=plan["judge_ref"])
    try:
        await _drive(run_id, ledger, templates, caller, plan, budget, now, concurrency)
    finally:
        await caller.aclose()


async def _drive(
    run_id: str,
    ledger: Ledger,
    templates: dict[str, Template],
    caller: ModelCaller,
    plan: dict,
    budget: float,
    now: bool,
    concurrency: int,
) -> None:
    judge = load_model(plan["judge_ref"])
    writer = load_model(plan["flash_ref"])
    guarded = plan["provider"] == "deepseek" and not now
    sem = asyncio.Semaphore(concurrency)
    stop = asyncio.Event()

    others = lane_total() - ledger.spent()
    ceiling = min(budget, LANE_CEILING - others)
    if ceiling < budget:
        print(f"lane ceiling leaves ${ceiling:.2f} of the ${budget:.2f} budget", file=sys.stderr)
    failures = 0

    def over_budget() -> bool:
        spent = ledger.spent()
        if spent >= ceiling and not stop.is_set():
            stop.set()
            print(f"budget reached: ${spent:.4f} of ${ceiling:.2f}", file=sys.stderr)
        return stop.is_set()

    async def one(row, played: bool) -> None:
        nonlocal failures
        template = templates[row["template_id"]]
        teachers: dict[Actor, Teacher] = {
            "p1": Teacher(row["teacher_p1"], load_model(row["teacher_p1"])),
            "p2": Teacher(row["teacher_p2"], load_model(row["teacher_p2"])),
        }
        async with sem:
            if over_budget():
                return
            if guarded and not in_window(datetime.now(UTC)):
                wait = seconds_until_open(datetime.now(UTC))
                print(f"outside the off-peak window, sleeping {wait / 3600:.1f} h", file=sys.stderr)
                await asyncio.sleep(wait)
            try:
                if not played:
                    await play_match(
                        template, match_from_row(template, row), teachers, caller, ledger, judge
                    )
                saboteur = Saboteur(template, row["match_id"], caller, ledger, judge, writer)
                ledger.mark_sabotaged(row["match_id"], await saboteur.run())
            except MatchAbandoned as e:
                print(f"abandoned: {e}", file=sys.stderr)
            except Exception:
                failures += 1
                traceback.print_exc()
            over_budget()

    if over_budget():
        return
    fresh = [one(row, False) for row in ledger.matches("active")]
    unsabotaged = [one(row, True) for row in ledger.ended_without_sabotage()]
    await asyncio.gather(*fresh, *unsabotaged)
    if failures:
        print(f"{failures} matches failed; resume to retry them", file=sys.stderr)
    elif not stop.is_set():
        ledger.finish_run(run_id)
    print(
        f"spent ${ledger.spent():.4f}, {len(ledger.matches('ended'))} matches ended",
        file=sys.stderr,
    )


def template_hash(template: Template) -> str:
    return hashlib.sha256(template.model_dump_json().encode()).hexdigest()[:16]


def check_hashes(plan: dict, templates: dict[str, Template]) -> None:
    """A run replays recorded calls by position, so the templates must not have changed."""
    for slug, stored in plan["judge_prompt_hash"].items():
        if judge_prompt_hash(templates[slug]) != stored:
            raise SystemExit(f"judge prompt for {slug} changed since the run was planned; refusing")
    for slug, stored in plan.get("template_hash", {}).items():
        if template_hash(templates[slug]) != stored:
            raise SystemExit(f"template {slug} changed since the run was planned; refusing")


def parse_counts(items: list[str]) -> dict[str, int]:
    counts = {}
    for item in items:
        slug, _, n = item.partition("=")
        counts[slug] = int(n)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    start = sub.add_parser("start")
    start.add_argument("run_id")
    start.add_argument("--matches", nargs="+", required=True, help="slug=count ...")
    start.add_argument("--provider", choices=list(PROVIDERS), default="fireworks")
    resume = sub.add_parser("resume")
    resume.add_argument("run_id")
    for p in (start, resume):
        p.add_argument("--budget", type=float, required=True, help="dollars; the run stops here")
        p.add_argument("--now", action="store_true", help="ignore DeepSeek's off-peak window")
        p.add_argument("--concurrency", type=int, help="matches in flight; default per provider")
        p.add_argument("--yes", action="store_true", help="skip the price confirmation")
    report = sub.add_parser("report")
    report.add_argument("run_id")
    args = ap.parse_args()

    ledger = Ledger(RUNS_DIR / f"{args.run_id}.db")
    if args.cmd == "report":
        text = card.write(args.run_id, ledger, CORPUS_DIR / args.run_id, classes())
        CARDS_DIR.mkdir(exist_ok=True)
        (CARDS_DIR / f"{args.run_id}.md").write_text(text)
        print(text)
        return

    if args.cmd == "start":
        counts = parse_counts(args.matches)
        templates = {slug: load_template(slug) for slug in counts}
        if ledger.plan(args.run_id) is not None:
            raise SystemExit(f"run {args.run_id} exists; use resume")
        provider = PROVIDERS[args.provider]
        ledger.create_run(
            args.run_id,
            {
                "matches": counts,
                "classes": {s: classes()[s] for s in counts},
                "provider": args.provider,
                "teachers": {provider["flash"]: FLASH_SHARE, LUNA: 1 - FLASH_SHARE},
                "judge_ref": provider["judge"],
                "flash_ref": provider["flash"],
                "judge_prompt_hash": {s: judge_prompt_hash(t) for s, t in templates.items()},
                "template_hash": {s: template_hash(t) for s, t in templates.items()},
                "sabotage_rate": RATE,
                "budget": args.budget,
                "created": datetime.now(UTC).isoformat(),
            },
        )
        plan_matches(ledger, templates, counts, random.Random(), provider["flash"])
    plan = ledger.plan(args.run_id)
    if plan is None:
        raise SystemExit(f"no run named {args.run_id}")
    templates = {slug: load_template(slug) for slug in plan["matches"]}
    check_hashes(plan, templates)
    pending = {}
    for row in ledger.matches("active"):
        pending[row["template_id"]] = pending.get(row["template_id"], 0) + 1
    base, peak = estimate(templates, pending)
    concurrency = args.concurrency or PROVIDERS[plan["provider"]]["concurrency"]
    price = (
        f"${base:.2f}"
        if plan["provider"] == "fireworks"
        else f"${base:.2f} off peak, ${peak:.2f} at peak"
    )
    print(
        f"{sum(pending.values())} matches to play on {plan['provider']} at concurrency "
        f"{concurrency}; estimate {price}; spent so far in this run ${ledger.spent():.4f}, "
        f"in the lane ${lane_total():.2f}; stop at ${args.budget:.2f}",
        file=sys.stderr,
    )
    if not args.yes and input("go? [y/N] ").strip().lower() != "y":
        return
    try:
        asyncio.run(drive(args.run_id, ledger, templates, args.budget, args.now, concurrency))
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
