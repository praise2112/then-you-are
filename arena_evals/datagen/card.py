"""The dataset card for one run: exports the corpus and reports what the ledger measured."""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from arena_core.state import STANDING, weighted_total
from arena_core.template import Template
from arena_evals.datagen.ledger import CallRow, Ledger
from arena_evals.datagen.records import PARSED, export
from arena_judge.schema import JudgeResponse, route_outcome

SHARE_SLACK = 0.05


def write(run_id: str, ledger: Ledger, corpus_dir: Path, templates: dict[str, Template]) -> str:
    plan = ledger.plan(run_id)
    if plan is None:
        raise SystemExit(f"no run named {run_id}")
    result = export(
        ledger, templates, plan["classes"], corpus_dir, sabotage_teacher=plan["flash_ref"]
    )
    matches = {row["match_id"]: row for row in ledger.matches()}
    lines = [f"# Run {run_id}", "", "## Matches", ""]
    status = Counter((m["template_id"], m["status"]) for m in matches.values())
    for (slug, st), n in sorted(status.items()):
        lines.append(f"- {slug}: {n} {st}")

    in_match: list[tuple[CallRow, str, int]] = []
    judge_versions: set[tuple[str, str]] = set()
    for match_id in matches:
        for call in ledger.calls(match_id):
            if call.role != "judge" or not call.payload["response"]:
                continue
            response = JudgeResponse.model_validate(call.payload["response"])
            template = templates[matches[match_id]["template_id"]]
            outcome = route_outcome(response.scoring)
            in_match.append(
                (call, outcome, weighted_total(response.scoring.scores, template.weights))
            )
            judge_versions.add((call.model, call.prompt_hash))

    lines += ["", "## Turns and acceptance", ""]
    by_template: Counter[str] = Counter()
    by_teacher: Counter[tuple[str, str]] = Counter()
    accepted_by_teacher: Counter[str] = Counter()
    for call, outcome, _ in in_match:
        slug = matches[call.match_id]["template_id"]
        teacher = matches[call.match_id][f"teacher_{call.actor}"]
        by_template[slug] += 1
        by_teacher[(teacher, outcome)] += 1
        if outcome in STANDING:
            accepted_by_teacher[teacher] += 1
    for slug, n in sorted(by_template.items()):
        lines.append(f"- {slug}: {n} verdicts in match")
    for teacher in sorted({t for t, _ in by_teacher}):
        total = sum(n for (t, _), n in by_teacher.items() if t == teacher)
        stood = accepted_by_teacher[teacher]
        lines.append(f"- {teacher}: {stood}/{total} moves stood ({stood / total:.0%})")
    accepted_total = sum(accepted_by_teacher.values())
    if accepted_total:
        flash = plan["flash_ref"]
        target = plan["teachers"][flash]
        share = accepted_by_teacher[flash] / accepted_total
        verdict = "inside" if abs(share - target) <= SHARE_SLACK else "OUTSIDE"
        lines.append(
            f"- Flash share of accepted turns: {share:.0%} ({verdict} the "
            f"{target - SHARE_SLACK:.0%} to {target + SHARE_SLACK:.0%} range)"
        )
    lines.append(f"- attempts: {dict(Counter(c.attempt for c, _, _ in in_match))}")
    lines.append(f"- judge versions (model, prompt hash): {sorted(judge_versions)}")

    lines += ["", "## Sabotage", ""]
    sab = ledger.sabotage_rows()
    per_kind: dict[str, list[bool]] = defaultdict(list)
    for row in sab:
        per_kind[row["kind"]].append(row["expected"] == row["outcome"])
    for kind, hits in sorted(per_kind.items()):
        lines.append(f"- {kind}: {sum(hits)}/{len(hits)} confirmed")
    if not sab:
        lines.append("- none")
    lines.append(f"- disputed records exported: {result.disputed}")

    lines += ["", "## Length", ""]
    for slug in templates:
        stood_len = [
            len(c.payload["move"])
            for c, o, _ in in_match
            if o in STANDING and matches[c.match_id]["template_id"] == slug
        ]
        fell_len = [
            len(c.payload["move"])
            for c, o, _ in in_match
            if o not in STANDING and matches[c.match_id]["template_id"] == slug
        ]
        pairs = [
            (len(c.payload["move"]), score)
            for c, o, score in in_match
            if o in STANDING and matches[c.match_id]["template_id"] == slug
        ]
        mean_stood = statistics.mean(stood_len) if stood_len else 0
        mean_fell = statistics.mean(fell_len) if fell_len else 0
        rho = (
            statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs], method="ranked")
            if len(pairs) >= 3 and len({p[0] for p in pairs}) > 1 and len({p[1] for p in pairs}) > 1
            else float("nan")
        )
        lines.append(
            f"- {slug}: mean chars stood {mean_stood:.0f}, fell {mean_fell:.0f}; "
            f"Spearman(length, weighted score) over stood moves = {rho:.2f}"
        )

    lines += ["", "## Corpus", ""]
    lines.append(f"- player.jsonl: {result.player}")
    lines.append(f"- judge.jsonl: {result.judge}")
    lines.append(f"- host.jsonl: {result.host}")
    lines.append(f"- disputed.jsonl: {result.disputed}")
    for reason, n in sorted(result.drops.items()):
        lines.append(f"- dropped, {reason}: {n}")
    lines.append(
        f"- salvaged verdicts kept out: {sum(1 for c, _, _ in in_match if c.attempt not in PARSED)}"
    )
    lines.append("- cross-judge agreement: not run")

    lines += ["", "## Cost", ""]
    calls = ledger.all_calls()
    by_model: dict[str, list[CallRow]] = defaultdict(list)
    for c in calls:
        by_model[c.model].append(c)
    for model, rows in sorted(by_model.items()):
        cost = sum(r.cost_usd for r in rows)
        lines.append(
            f"- {model}: {len(rows)} calls, ${cost:.4f}, "
            f"${cost / len(rows):.5f} per call, tokens in {sum(r.tokens_in for r in rows)}, "
            f"out {sum(r.tokens_out for r in rows)}"
        )
    lines.append(f"- total: ${ledger.spent():.4f}")

    lines += ["", "## Plan", "", "```json", json.dumps(plan, indent=1), "```", ""]
    return "\n".join(lines)
