"""Generate template variants for a class: sample cells, sketch and write with Luna, lint,
dedup on the rendered student prompt, rank with Flash, record the pool index.

uv run python -m arena_evals.variants.generate run counter --budget 1 [--now] [--threshold 0.8]
uv run python -m arena_evals.variants.generate similarity
uv run python -m arena_evals.variants.generate promote <slug>
uv run python -m arena_evals.variants.generate demote <slug>
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import random
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import TypeAdapter, ValidationError

from arena_core.template import TEMPLATES_DIR, Strict, Template, load_template_file, load_templates
from arena_evals.common import embed_texts, load_model, make_caller, require_window, with_backoff
from arena_evals.grow_seeds import cosine, nearest, seed_line
from arena_evals.variants.spec import ClassSpec, Voice, field_value, load_spec
from arena_judge.caller import CallError, ModelCaller, extract_json
from arena_judge.prompt import render_opponent_system

POOL_DIR = Path(__file__).parent / "pool"
RAW_DIR = POOL_DIR / "raw"
INDEX_PATH = POOL_DIR / "index.json"
SKETCHES = 6
KEEP = 3
HELDOUT_EVERY = 5
WRITER = "opponent-luna"
RANKER = "opponent-fireworks"
RATING_GUIDANCE = {
    "family": "no profanity, no innuendo, jokes target moves only",
    "mild": "playful jabs at the player, light innuendo, no profanity",
    "edgy": "profanity and adult innuendo allowed, roast-level jabs at the player",
}


class Cell(Strict):
    klass: str
    parent: str
    theme: str
    voice: Voice
    content_rating: str
    weights: list[int]

    @property
    def id(self) -> str:
        return hashlib.sha1(self.model_dump_json().encode()).hexdigest()[:8]


class Sketch(Strict):
    slug: str
    title: str
    premise: str
    criterion: str
    move_shape: str
    first_card: str


class Sketches(Strict):
    sketches: list[Sketch]


Stage = Literal["lint", "dedup", "rank", "pilot", "consistency", "seeds"]


class Entry(Strict):
    """One pool candidate's provenance, the last stage it reached, and why it stopped there."""

    klass: str
    parent: str
    cell: Cell
    stage: Stage
    reject: str | None = None
    rank: float | None = None
    split: str | None = None
    stats: dict | None = None


@dataclass
class Tally:
    spent: float = 0.0
    calls: int = 0
    rejects: Counter[str] = field(default_factory=Counter)


def sample_cells(spec: ClassSpec, rng: random.Random) -> list[Cell]:
    axes = spec.axes
    themes = rng.sample(axes.theme, min(spec.cells, len(axes.theme)))
    cells = []
    for i in range(spec.cells):
        rating = rng.choices(list(axes.content_rating), list(axes.content_rating.values()))[0]
        voice = rng.choice(axes.voice)
        while rating == "family" and (
            voice.bite in axes.family_excludes.get("bite", [])
            or voice.explanation_adj in axes.family_excludes.get("explanation_adj", [])
        ):
            voice = rng.choice(axes.voice)
        cells.append(
            Cell(
                klass=spec.name,
                parent=spec.examples[i % len(spec.examples)],
                theme=themes[i % len(themes)],
                voice=voice,
                content_rating=rating,
                weights=rng.choice(axes.weights),
            )
        )
    return cells


def parent_shape(parent: Template, spec: ClassSpec) -> str:
    """The example as JSON with the fixed fields and the demo removed, seeds, examples and
    recipe axes trimmed so the shape shows without the bulk."""
    body = parent.model_dump(exclude={"demo", "schema_version"})
    for path in spec.fixed:
        del_path(body, path)
    body["seed_pool"] = body["seed_pool"][:3]
    body["examples"] = body["examples"][:4]
    if body.get("seed_recipe"):
        body["seed_recipe"]["axes"] = {k: v[:3] for k, v in body["seed_recipe"]["axes"].items()}
    return json.dumps(body, indent=1, ensure_ascii=False)


def del_path(body: dict, path: str) -> None:
    *parents, leaf = path.split(".")
    node = body
    for part in parents:
        node = node.get(part) or {}
    node.pop(leaf, None)


def set_path(body: dict, path: str, value) -> None:
    *parents, leaf = path.split(".")
    node = body
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value


def sketch_messages(spec: ClassSpec, parent: Template, cell: Cell) -> list[dict]:
    tests = "\n".join(f"- {t}" for t in spec.tests)
    rules = "\n".join(f"- {r.strip()}" for r in parent.rules)
    system = (
        "You design two-player word games judged by a language model. Every game in this "
        f"class keeps the mechanic of the worked example, {parent.title}: mode {parent.mode}, "
        f"{parent.win_condition}, moves up to {parent.move_constraints.max_chars} characters, "
        f'the judge asks whether a move "{parent.criterion.verb}" what stands.\n\n'
        f"{parent.title}: {parent.premise.strip()}\n"
        f"What plays: {parent.criterion.description.strip()}\n"
        f"Rules:\n{rules}\n\nA new game must pass these tests:\n{tests}\n\n"
        'Reply with JSON only: {"sketches": [{"slug": "kebab-case", "title": "...", '
        '"premise": "two or three sentences to a stranger", "criterion": "one sentence, what '
        'the judge checks", "move_shape": "what one move looks like, with an example", '
        '"first_card": "one opening card"}, ...]}, best first by the first test.'
    )
    user = (
        f"Theme: {cell.theme}. Host voice: {cell.voice.commentary_adj} commentary, "
        f"{cell.voice.explanation_adj} explanations, bite {cell.voice.bite}. Content rating: "
        f"{cell.content_rating} ({RATING_GUIDANCE[cell.content_rating]}).\n"
        f"Give {SKETCHES} sketches. Each is a different game, none a re-skin of {parent.title} "
        "or of another sketch. Everyday words a twelve-year-old knows."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def write_messages(spec: ClassSpec, parent: Template, cell: Cell, sketch: Sketch) -> list[dict]:
    names = ", ".join(r.name for r in parent.rubric)
    system = (
        "You write the full template, as JSON, for a two-player word game judged by a "
        f"language model. Imitate the shape and length of this template for {parent.title}. "
        "Its mechanic fields are removed because they are set for you; do not write them.\n\n"
        f"```json\n{parent_shape(parent, spec)}\n```\n\n"
        "Write every key shown, for the new game. Rules:\n"
        f"- rubric names and order are exactly: {names}. Rewrite descriptions and anchors.\n"
        "- host.tone, host.bite and host.ruling_generosity are set for you. Write voice_rules, "
        f"good_headlines and bad_headlines in a {cell.voice.commentary_adj} voice with "
        f"{cell.voice.explanation_adj} explanations, bite {cell.voice.bite}, content rating "
        f"{cell.content_rating} ({RATING_GUIDANCE[cell.content_rating]}). Identity-based abuse "
        "is banned.\n"
        "- examples: at least four, at least two with verdict fail, scores for every rubric name.\n"
        "- seed_pool: exactly three cards in the theme, no quotation marks inside a card"
        + (
            ", each with a detail (the public line) and a hidden truth only the judge sees.\n"
            if parent.guess
            else ".\n"
        )
        + '- seed_recipe: card_shape is exactly "short_form" (a noun phrase with an article) or '
        '"sentence"; max_words for one card; axes is an object of three axis names, each a list '
        "of at least eight values (the example shows three per axis, write eight or more); tests "
        "is five sentences a card must pass, in the style of the example's tests.\n"
        "- keep the {standing_form} slot in judge_out_text and validation_messages.nudge, and "
        "the {token} slot in labels.compose.\n"
        "- criterion.judge_notes cover the first move and every failure the criterion names.\n"
        "- plain spoken English throughout, no dashes as punctuation.\n"
        "Reply with one JSON object only, no fences, no commentary."
    )
    user = (
        f"Theme: {cell.theme}.\nSketch to build:\n{sketch.model_dump_json(indent=1)}\n"
        f"The slug is {sketch.slug}."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def rank_messages(spec: ClassSpec, template: Template) -> list[dict]:
    tests = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(spec.tests))
    system = (
        "You score a two-player word game on the tests below, 0 to 4 each, as a stranger who "
        "has just read the game and nothing else. 4 means the test is passed beyond doubt, 0 "
        f"means it fails.\n\nTests:\n{tests}\n\n"
        'Reply with JSON only: {"scores": [4, 3, ...]} in test order.'
    )
    rules = "\n".join(f"- {r.strip()}" for r in template.rules)
    user = f"{render_opponent_system(template)}\n\nRULES SHOWN TO PLAYERS\n{rules}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_template(spec: ClassSpec, parent: Template, cell: Cell, body: dict) -> Template:
    """Fixed fields from the parent, knobs from the cell, demo from the first example."""
    body["schema_version"] = parent.schema_version
    for path in spec.fixed:
        value = field_value(parent, path)
        set_path(body, path, value.model_dump() if isinstance(value, Strict) else value)
    names = [r.get("name") for r in body.get("rubric") or []]
    if names != [r.name for r in parent.rubric]:
        raise ValueError(f"rubric names {names} differ from the parent")
    for entry, weight in zip(body["rubric"], cell.weights, strict=True):
        entry["weight"] = weight
    host = body.setdefault("host", {})
    host["tone"] = {
        "commentary_adj": cell.voice.commentary_adj,
        "explanation_adj": cell.voice.explanation_adj,
    }
    host["bite"] = cell.voice.bite
    host["ruling_generosity"] = cell.voice.ruling_generosity
    body["demo"] = scaffold_demo(body, parent)
    return Template.model_validate(body)


def scaffold_demo(body: dict, parent: Template) -> dict:
    seed = body["seed_pool"][0]
    example = body["examples"][0]
    return {
        "opening": {"token": seed["opening_token"], "emoji": seed["opening_emoji"]},
        "moves": [
            {"actor": "p1", "text": example["previous_move"], "emoji": seed["opening_emoji"]},
            {
                "actor": "p2",
                "text": example["move"],
                "emoji": parent.demo.moves[1].emoji,
                "scores": example["scores"],
            },
        ],
        "headline": body["host"]["good_headlines"][0],
        "openings": [
            {"token": s["opening_token"], "examples": [example["move"]]}
            for s in body["seed_pool"][:2]
        ],
    }


def dump_template(template: Template) -> str:
    """YAML with the seed pool last, one flow line per seed, the layout grow_seeds extends."""
    body = template.model_dump(exclude={"seed_pool"})
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100)
    lines = "".join(seed_line(s) for s in template.seed_pool)
    return f"{text}seed_pool:\n{lines}"


INDEX = TypeAdapter(dict[str, Entry])


def load_index() -> dict[str, Entry]:
    return INDEX.validate_json(INDEX_PATH.read_bytes()) if INDEX_PATH.exists() else {}


def save_index(entries: dict[str, Entry]) -> None:
    """Merges `entries` into the index on disk under a file lock, so runs in parallel keep
    each other's changes, and writes it through a temp file so a crash never truncates it."""
    POOL_DIR.mkdir(exist_ok=True)
    with INDEX_PATH.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = load_index() | entries
        tmp = INDEX_PATH.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(INDEX.dump_json(dict(sorted(index.items())), indent=1) + b"\n")
        tmp.replace(INDEX_PATH)


def pool_path(klass: str, slug: str) -> Path:
    return POOL_DIR / klass / f"{slug}.yaml"


class Generator:
    def __init__(self, spec: ClassSpec, parents: dict[str, Template], caller: ModelCaller):
        self.spec = spec
        self.parents = parents
        self.caller = caller
        self.luna = load_model(WRITER)
        self.ranker = load_model(RANKER)
        self.sem = asyncio.Semaphore(16)
        self.tally = Tally()
        self.index = load_index()
        self.shipped = load_templates()

    async def _cached(self, path: Path, spec, messages: list[dict], **extra) -> str:
        """The model's raw reply, from the raw cache when this call already ran."""
        if path.exists():
            return path.read_text()
        async with self.sem:
            result = await with_backoff(
                lambda: self.caller.complete(spec, messages, reasoning={"enabled": False}, **extra)
            )
        self.tally.calls += 1
        self.tally.spent += result.cost_usd
        if result.text.strip():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result.text)
        return result.text

    async def run_cell(self, cell: Cell) -> list[str]:
        """Sketch, write and lint one cell; returns the slugs that linted."""
        parent = self.parents[cell.parent]
        raw_dir = RAW_DIR / cell.klass
        try:
            raw = await self._cached(
                raw_dir / f"{cell.id}.sketches.json",
                self.luna,
                sketch_messages(self.spec, parent, cell),
                max_tokens=2000,
            )
        except CallError:
            self.tally.rejects["call_error"] += 1
            return []
        parsed = extract_json(raw)
        try:
            sketches = Sketches.model_validate(parsed).sketches[:KEEP] if parsed else []
        except ValidationError:
            sketches = []
        if not sketches:
            self.tally.rejects["sketches unparsable"] += 1
            return []
        written = await asyncio.gather(
            *(self.write_one(cell, parent, k, s) for k, s in enumerate(sketches))
        )
        return [slug for slug in written if slug]

    async def write_one(self, cell: Cell, parent: Template, k: int, sketch: Sketch) -> str | None:
        """Writes and lints one candidate; None when it failed or an earlier run already has it."""
        if f"{cell.id}.{k}" in self.index:
            return None
        try:
            raw = await self._cached(
                RAW_DIR / cell.klass / f"{cell.id}.{k}.json",
                self.luna,
                write_messages(self.spec, parent, cell, sketch),
                max_tokens=7000,
                response_format={"type": "json_object"},
            )
        except CallError:
            self.tally.rejects["call_error"] += 1
            return None
        body = extract_json(raw)
        if not body:
            self.tally.rejects["json unparsable"] += 1
            return None
        slug = str(body.get("slug") or sketch.slug)
        taken = slug in self.shipped or (slug in self.index and self.index[slug].cell != cell)
        if taken:
            slug = f"{slug}-{cell.id[:4]}-{k}"
        if slug in self.index:
            return None
        body["slug"] = slug
        try:
            template = build_template(self.spec, parent, cell, body)
        except (ValidationError, ValueError, KeyError, IndexError, TypeError) as e:
            self.tally.rejects["lint"] += 1
            self.index[f"{cell.id}.{k}"] = Entry(
                klass=cell.klass, parent=cell.parent, cell=cell, stage="lint", reject=str(e)[:300]
            )
            return None
        path = pool_path(cell.klass, slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_template(template))
        load_template_file(path)
        self.index[slug] = Entry(klass=cell.klass, parent=cell.parent, cell=cell, stage="lint")
        return slug

    def prompt_of(self, slug: str) -> str:
        return render_opponent_system(load_template_file(pool_path(self.spec.name, slug)))

    def entries(self) -> dict[str, Entry]:
        """This class's index entries, the only ones a generator run changes."""
        return {s: e for s, e in self.index.items() if e.klass == self.spec.name}

    def live(self, stage: Stage) -> list[str]:
        """Slugs of this class that passed `stage` and wait for the next one."""
        return [
            s
            for s, e in self.index.items()
            if e.klass == self.spec.name and e.stage == stage and e.reject is None
        ]

    async def dedup(self, threshold: float) -> None:
        """Greedy by cell order: a candidate whose student prompt sits within `threshold` cosine
        of the shipped games, the pool, or an earlier candidate is dropped."""
        known = {slug: render_opponent_system(t) for slug, t in self.shipped.items()}
        for slug, entry in self.index.items():
            if entry.klass == self.spec.name and entry.stage != "lint" and entry.reject is None:
                known[slug] = self.prompt_of(slug)
        fresh = self.live("lint")
        texts = [self.prompt_of(s) for s in fresh]
        vectors = await embed_texts(self.caller, [*known.values(), *texts], self.sem)
        known_vecs = dict(zip(known, vectors[: len(known)], strict=True))
        for slug, vec in zip(fresh, vectors[len(known) :], strict=True):
            closest, score = nearest(vec, known_vecs)
            print(f"  {slug:32s} nearest {closest:32s} {score:.3f}", file=sys.stderr)
            self.index[slug].stage = "dedup"
            if score >= threshold:
                self.index[slug].reject = f"dup of {closest} at {score:.3f}"
                self.tally.rejects["dup"] += 1
                save_index(self.entries())
                pool_path(self.spec.name, slug).unlink()
                continue
            known_vecs[slug] = vec

    async def rank(self) -> None:
        """One Flash call per survivor scores the class tests; the top half per cell stays."""
        slugs = self.live("dedup")

        async def score(slug: str) -> float:
            template = load_template_file(pool_path(self.spec.name, slug))
            raw = await self._cached(
                RAW_DIR / self.spec.name / f"{slug}.rank.json",
                self.ranker,
                rank_messages(self.spec, template),
                max_tokens=200,
                temperature=0.0,
            )
            parsed = extract_json(raw) or {}
            scores = parsed.get("scores") or []
            return float(sum(scores)) if len(scores) == len(self.spec.tests) else 0.0

        totals = dict(zip(slugs, await asyncio.gather(*(score(s) for s in slugs)), strict=True))
        by_cell: dict[str, list[str]] = {}
        for slug in slugs:
            self.index[slug].rank = totals[slug]
            by_cell.setdefault(self.index[slug].cell.id, []).append(slug)
        for members in by_cell.values():
            members.sort(key=lambda s: -totals[s])
            keep = math.ceil(len(members) / 2)
            for slug in members:
                self.index[slug].stage = "rank"
            for slug in members[keep:]:
                self.index[slug].reject = f"rank {totals[slug]:.0f} below the cell's top half"
                self.tally.rejects["rank"] += 1
        save_index(self.entries())
        for slug in slugs:
            if self.index[slug].reject:
                pool_path(self.spec.name, slug).unlink(missing_ok=True)
        unsplit = sorted(s for s in self.live("rank") if self.index[s].split is None)
        for i, slug in enumerate(unsplit):
            self.index[slug].split = (
                "heldout" if i % HELDOUT_EVERY == HELDOUT_EVERY - 1 else "train"
            )


async def generate(klass: str, budget: float, threshold: float, seed: int) -> Tally:
    spec, parents = load_spec(klass)
    caller = make_caller()
    gen = Generator(spec, parents, caller)
    cells = sample_cells(spec, random.Random(seed))
    try:
        probe, rest = cells[0], cells[1:]
        before = gen.tally.spent
        linted = await gen.run_cell(probe)
        per_cell = gen.tally.spent - before
        projected = per_cell * len(cells)
        print(
            f"probe cell: {len(linted)} linted, ${per_cell:.4f}; projected ${projected:.2f} "
            f"for {len(cells)} cells against a budget of ${budget:.2f}",
            file=sys.stderr,
        )
        if projected > budget:
            print("projected cost is over the budget, stopping after the probe", file=sys.stderr)
        else:
            for i in range(0, len(rest), 6):
                if gen.tally.spent >= budget:
                    print(f"budget reached at ${gen.tally.spent:.2f}, stopping", file=sys.stderr)
                    break
                await asyncio.gather(*(gen.run_cell(c) for c in rest[i : i + 6]))
                save_index(gen.entries())
        save_index(gen.entries())
        if gen.tally.spent >= budget:
            print("budget spent on writing; dedup and rank wait for the next run", file=sys.stderr)
        else:
            if gen.live("lint"):
                await gen.dedup(threshold)
                save_index(gen.entries())
            if gen.live("dedup"):
                await gen.rank()
                save_index(gen.entries())
    finally:
        save_index(gen.entries())
        await caller.aclose()
    return gen.tally


async def similarity() -> None:
    """Pairwise cosine of the shipped games' student prompts, the floor for the dedup threshold."""
    caller = make_caller()
    try:
        prompts = {slug: render_opponent_system(t) for slug, t in load_templates().items()}
        vectors = dict(
            zip(
                prompts,
                await embed_texts(caller, list(prompts.values()), asyncio.Semaphore(4)),
                strict=True,
            )
        )
    finally:
        await caller.aclose()
    slugs = list(vectors)
    for a in slugs:
        row = " ".join(f"{cosine(vectors[a], vectors[b]):.3f}" for b in slugs)
        print(f"{a:16s} {row}")


def promote(slug: str) -> Path:
    matches = list(POOL_DIR.glob(f"*/{slug}.yaml"))
    if len(matches) != 1:
        raise SystemExit(f"{slug}: expected one pool file, found {len(matches)}")
    dst = TEMPLATES_DIR / slug / "v1.yaml"
    if dst.exists() and slug not in load_index():
        raise SystemExit(f"{slug} is a shipped game; refusing to overwrite it")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(matches[0], dst)
    load_template_file(dst)
    return dst


def demote(slug: str) -> None:
    if slug not in load_index():
        raise SystemExit(f"{slug} is not a pool variant; refusing to remove a shipped game")
    shutil.rmtree(TEMPLATES_DIR / slug)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("klass")
    run.add_argument("--budget", type=float, required=True, help="dollars; the run stops here")
    run.add_argument("--threshold", type=float, default=0.8, help="cosine above this is a dup")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--now", action="store_true", help="run outside the off-peak window")
    sub.add_parser("similarity")
    sub.add_parser("promote").add_argument("slug")
    sub.add_parser("demote").add_argument("slug")
    args = ap.parse_args()
    if args.cmd == "run":
        require_window(args.now)
        tally = asyncio.run(generate(args.klass, args.budget, args.threshold, args.seed))
        stages = Counter(
            (e.stage, "reject" if e.reject else "pass")
            for e in load_index().values()
            if e.klass == args.klass
        )
        print(
            f"calls {tally.calls}, cost ${tally.spent:.4f}, stages {dict(stages)}", file=sys.stderr
        )
        for reason, n in tally.rejects.most_common():
            print(f"  rejected {n:3d}  {reason}", file=sys.stderr)
    elif args.cmd == "similarity":
        asyncio.run(similarity())
    elif args.cmd == "promote":
        print(promote(args.slug))
    else:
        demote(args.slug)


if __name__ == "__main__":
    main()
