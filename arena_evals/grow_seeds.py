"""Grow a template's seed pool: sample the recipe grid, generate, dedup, append.

uv run python -m arena_evals.grow_seeds --template then-i-am --target 500 [--dry-run]
uv run python -m arena_evals.grow_seeds --path arena_evals/variants/pool/counter/x.yaml --target 60
"""

import argparse
import asyncio
import itertools
import json
import math
import random
import re
import secrets
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from arena_core.template import TEMPLATES_DIR, Seed, Template, load_template_file
from arena_evals.common import (
    EMBED_BATCH,
    embed_texts,
    make_caller,
    require_window,
    with_backoff,
)
from arena_judge.caller import ModelCaller, extract_json

CACHE_DIR = Path(__file__).parent / "seeds"
CANDIDATES_PER_CELL = 6
KEEP_PER_CELL = 3
SEED_WRITER = "opponent-v1"
EMPTY_ROUNDS = 3
AVOID_MAX = 150
ARTICLES = ("a ", "an ", "the ")
STOP = {"of", "the", "a", "an"}
ING_NOUNS = {
    "morning",
    "evening",
    "ring",
    "string",
    "king",
    "thing",
    "wing",
    "swing",
    "spring",
    "pudding",
    "building",
    "ceiling",
    "wedding",
    "stocking",
    "earring",
    "herring",
    "dumpling",
    "duckling",
    "stuffing",
    "frosting",
    "icing",
    "bedding",
    "lightning",
}


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening: str
    emoji: str
    answer: str
    detail: str = ""
    hidden: str = ""


@dataclass(frozen=True)
class Shape:
    """What a card looks like: short forms get article checks, every card a word cap, a guess
    game a detail and truth per card, and cards naming distinct things distinct head nouns."""

    short_form: bool
    max_words: int
    needs_truth: bool
    distinct_heads: bool = True


def card_shape(template: Template) -> Shape:
    """The shape of the game's own cards: the short-form and head-noun checks apply only when
    its first three cards, written with the game, already pass them."""
    recipe = template.seed_recipe
    assert recipe is not None
    first = [s.opening_token for s in template.seed_pool[:3]]
    kept = sum(short_form_reason(t) is None for t in first)
    return Shape(
        short_form=recipe.card_shape == "short_form" and kept * 2 > len(first),
        max_words=recipe.max_words,
        needs_truth=template.guess is not None,
        distinct_heads=len({head_noun(t) for t in first}) == len(first),
    )


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[Candidate]


@dataclass
class Report:
    accepted: list[Seed]
    rejects: Counter[str]
    calls: int
    cost_usd: float
    stalled: bool = False


def head_noun(opening: str) -> str:
    """The last word that is not a stop word, singular: "a house of cards" gives "house"."""
    words = [w for w in re.findall(r"[a-z]+", opening.lower()) if w not in STOP]
    if not words:
        return ""
    # "of" clauses name the whole by the word before them.
    text = opening.lower()
    if " of " in text:
        before = [w for w in re.findall(r"[a-z]+", text.split(" of ")[0]) if w not in STOP]
        if before:
            words = before
    return singular(words[-1])


def singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def is_single_emoji(text: str) -> bool:
    """One picture: a lone symbol, or symbols joined into one with zero-width joiners."""
    if not text or len(text) > 12:
        return False
    if not all(unicodedata.category(ch) in {"So", "Sk", "Mn", "Me", "Cf"} for ch in text):
        return False
    symbols = sum(unicodedata.category(ch) == "So" for ch in text)
    joiners = text.count("\u200d")
    return symbols - joiners == 1


def short_form_reason(text: str) -> str | None:
    """Why a card breaks the short-form style ("a hammer"), or None when it keeps it."""
    if not text.lower().startswith(ARTICLES):
        return "no article"
    if re.match(r"a [aeio]|an [^aeiouh]", text.lower()):
        return "wrong article"
    last = text.split()[-1].lower()
    if last.endswith("ing") and last not in ING_NOUNS:
        return "dangling verb"
    if text != text.lower() and not re.search(
        r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", text
    ):
        return "capitalised"
    return None


def mechanical_reason(cand: Candidate, shape: Shape) -> str | None:
    """Why a candidate fails the cheap checks, or None when it passes them all."""
    text = cand.opening.strip()
    if not text:
        return "empty"
    if len(text.split()) > shape.max_words:
        return "too long"
    if shape.short_form and (reason := short_form_reason(text)) is not None:
        return reason
    if not is_single_emoji(cand.emoji):
        return "bad emoji"
    if len(cand.answer.split()) < 2:
        return "no answer"
    if shape.needs_truth and not (cand.detail.strip() and cand.hidden.strip()):
        return "no truth"
    return None


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def nearest(vec: list[float], others: dict[str, list[float]]) -> tuple[str, float]:
    """The closest known vector by cosine and its score; ("", -1.0) when there is none."""
    return max(
        ((k, cosine(vec, v)) for k, v in others.items()), key=lambda p: p[1], default=("", -1.0)
    )


def generator_messages(template: Template, cell: dict[str, str], avoid: list[str]) -> list[dict]:
    recipe = template.seed_recipe
    assert recipe is not None
    shape = card_shape(template)
    tests = "\n".join(f"- {t}" for t in recipe.tests)
    cell_text = ", ".join(f"{axis}: {value}" for axis, value in cell.items())
    avoid_text = "; ".join(avoid) if avoid else "(none yet)"
    first = template.seed_pool[0]
    example = {"opening": first.opening_token, "emoji": first.opening_emoji, "answer": "..."}
    if shape.needs_truth:
        example |= {"detail": first.detail, "hidden": first.hidden}
    truth = (
        ' "detail" is the public line shown with the card and "hidden" is the truth only the '
        "judge sees, real and checkable."
        if shape.needs_truth
        else ""
    )
    system = (
        f"You write opening cards for a word game called {template.title}. "
        f"{template.premise.strip()}\n"
        f"Every card must pass these tests:\n{tests}\n"
        f'Reply with JSON only: {{"candidates": [{json.dumps(example, ensure_ascii=False)}, '
        "...]}. "
        'The emoji is one emoji that shows the card. "answer" is the plain first move a '
        f"stranger would make in reply.{truth}"
    )
    short = ""
    if shape.short_form:
        short = "At least three are one word after the article, like a wasp. "
    user = (
        f"Give {CANDIDATES_PER_CELL} cards from this corner of the world: {cell_text}.\n"
        f"Each one a different thing, none of them a cousin of another. {short}"
        f"Order them by how much a stranger would want to answer, best first.\n"
        f"Already taken, do not repeat or paraphrase: {avoid_text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_batch(raw: str) -> Batch | None:
    data = extract_json(raw)
    if data is None:
        return None
    try:
        return Batch.model_validate(data)
    except ValidationError:
        return None


class Grower:
    def __init__(self, template: Template, caller: ModelCaller, concurrency: int, threshold: float):
        self.template = template
        self.caller = caller
        self.sem = asyncio.Semaphore(concurrency)
        self.threshold = threshold
        self.calls = 0
        self.cost = 0.0
        self.pool_vectors: dict[str, list[float]] = {}

    async def generate_cell(self, cell: dict[str, str], avoid: list[str]) -> list[Candidate]:
        messages = generator_messages(self.template, cell, avoid)
        async with self.sem:
            result = await with_backoff(
                lambda: self.caller.complete(
                    self.caller.opponent_spec,
                    messages,
                    max_tokens=1200,
                    reasoning={"enabled": False},
                )
            )
        self.calls += 1
        self.cost += result.cost_usd
        batch = parse_batch(result.text)
        return batch.candidates[:KEEP_PER_CELL] if batch else []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = await embed_texts(self.caller, texts, self.sem)
        self.calls += math.ceil(len(texts) / EMBED_BATCH)
        return vectors

    async def load_pool_vectors(self) -> None:
        cache = CACHE_DIR / f"{self.template.slug}.json"
        cached: dict[str, list[float]] = json.loads(cache.read_text()) if cache.exists() else {}
        tokens = [s.opening_token for s in self.template.seed_pool]
        missing = [t for t in tokens if t not in cached]
        if missing:
            for token, vec in zip(missing, await self.embed(missing), strict=True):
                cached[token] = vec
        self.pool_vectors = {t: cached[t] for t in tokens}
        CACHE_DIR.mkdir(exist_ok=True)
        cache.write_text(json.dumps(self.pool_vectors))

    async def round(self, cells: list[dict[str, str]], rejects: Counter[str]) -> list[Seed]:
        pool = self.template.seed_pool
        tokens = [s.opening_token for s in pool]
        avoid = tokens if len(tokens) <= AVOID_MAX else random.sample(tokens, AVOID_MAX)
        raw = await asyncio.gather(*(self.generate_cell(c, avoid) for c in cells))
        shape = card_shape(self.template)
        heads = {head_noun(s.opening_token) for s in pool}
        seen_tokens = {s.opening_token.lower() for s in pool}
        survivors: list[Candidate] = []
        for cand in itertools.chain.from_iterable(raw):
            cand.opening = cand.opening.strip()
            if shape.short_form:
                cand.opening = cand.opening.rstrip(".")
            if (reason := mechanical_reason(cand, shape)) is not None:
                rejects[reason] += 1
                continue
            if cand.opening.lower() in seen_tokens:
                rejects["exact repeat"] += 1
                continue
            head = head_noun(cand.opening)
            if shape.short_form and shape.distinct_heads and head in heads:
                rejects["same head noun"] += 1
                continue
            heads.add(head)
            seen_tokens.add(cand.opening.lower())
            survivors.append(cand)
        if not survivors:
            return []
        vectors = await self.embed([c.opening for c in survivors])
        accepted: list[Seed] = []
        known = dict(self.pool_vectors)
        for cand, vec in zip(survivors, vectors, strict=True):
            token, score = nearest(vec, known)
            if score >= self.threshold:
                rejects[f"too close ({token})"] += 1
                continue
            known[cand.opening] = vec
            accepted.append(
                Seed(
                    opening_token=cand.opening,
                    opening_emoji=cand.emoji,
                    detail=cand.detail.strip(),
                    hidden=cand.hidden.strip(),
                )
            )
        self.pool_vectors = known
        return accepted


def all_cells(template: Template) -> list[dict[str, str]]:
    recipe = template.seed_recipe
    assert recipe is not None
    axes = list(recipe.axes)
    cells = [
        dict(zip(axes, combo, strict=True)) for combo in itertools.product(*recipe.axes.values())
    ]
    secrets.SystemRandom().shuffle(cells)
    return cells


def seed_line(seed: Seed) -> str:
    """One flow-style pool line, the layout every template file uses for its cards."""
    fields = {"opening_token": seed.opening_token, "opening_emoji": seed.opening_emoji}
    if seed.detail or seed.hidden:
        fields |= {"detail": seed.detail, "hidden": seed.hidden}
    inner = ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items())
    return f"  - {{ {inner} }}\n"


def append_to_pool(template: Template, seeds: list[Seed], path: Path) -> None:
    """Add seeds as flow-style lines after the last pool entry, keeping the file's own layout."""
    lines = path.read_text().splitlines(keepends=True)
    last = template.seed_pool[-1]
    hits = [
        i
        for i, line in enumerate(lines)
        if line.startswith("  - {")
        and (yaml.safe_load(line[4:]) or {}).get("opening_token") == last.opening_token
    ]
    if len(hits) != 1:
        raise ValueError(f"could not find the last seed line in {path}")
    lines[hits[0] + 1 : hits[0] + 1] = [seed_line(s) for s in seeds]
    path.write_text("".join(lines))


async def grow(
    path: Path,
    target: int,
    concurrency: int,
    threshold: float,
    dry_run: bool,
    budget: float | None = None,
) -> Report:
    """Grows the pool by `target` seeds, appending to the file after every round so a crash
    keeps what was paid for."""
    template = load_template_file(path)
    if template.seed_recipe is None:
        raise SystemExit(f"{path} has no seed_recipe")
    caller = make_caller(opponent_ref=SEED_WRITER)
    grower = Grower(template, caller, concurrency, threshold)
    rejects: Counter[str] = Counter()
    accepted: list[Seed] = []
    stalled = False
    try:
        await grower.load_pool_vectors()
        cells = all_cells(template)
        probe = await grower.generate_cell(cells[0], [])
        if not probe:
            print("the generator returned nothing parsable on a probe call", file=sys.stderr)
            return Report(accepted, rejects, grower.calls, grower.cost)
        empty = 0
        while len(accepted) < target and cells:
            shortfall = target - len(accepted)
            take = min(len(cells), max(1, math.ceil(2 * shortfall / KEEP_PER_CELL)))
            batch, cells = cells[:take], cells[take:]
            got = await grower.round(batch, rejects)
            empty = 0 if got else empty + 1
            if empty == EMPTY_ROUNDS:
                print(f"{EMPTY_ROUNDS} rounds in a row yielded nothing, stopping", file=sys.stderr)
                stalled = True
                break
            if not got:
                continue
            got = got[: target - len(accepted)]
            accepted.extend(got)
            if not dry_run:
                append_to_pool(template, got, path)
            template = template.model_copy(update={"seed_pool": [*template.seed_pool, *got]})
            grower.template = template
            print(
                f"round: {len(batch)} cells, +{len(got)}, total {len(accepted)}/{target}",
                file=sys.stderr,
            )
            if budget is not None and grower.cost >= budget:
                print(f"budget reached at ${grower.cost:.4f}", file=sys.stderr)
                break
        else:
            stalled = len(accepted) < target
    finally:
        await caller.aclose()
    if accepted and not dry_run:
        load_template_file(path)  # the file must still lint
    return Report(accepted, rejects, grower.calls, grower.cost, stalled)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", help="a shipped game's slug")
    ap.add_argument("--path", type=Path, help="any template YAML, for pool variants")
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=64)
    ap.add_argument(
        "--threshold", type=float, default=0.65, help="cosine above this is a duplicate"
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", action="store_true", help="run outside the off-peak window")
    args = ap.parse_args()
    if (args.template is None) == (args.path is None):
        ap.error("give exactly one of --template or --path")
    require_window(args.now)
    path = args.path or TEMPLATES_DIR / args.template / "v1.yaml"
    report = asyncio.run(grow(path, args.target, args.concurrency, args.threshold, args.dry_run))
    for seed in report.accepted:
        print(f"{seed.opening_emoji} {seed.opening_token}")
    one_word = sum(len(s.opening_token.split()) == 2 for s in report.accepted)
    print(
        f"\naccepted {len(report.accepted)} ({one_word} one word), "
        f"calls {report.calls}, cost ${report.cost_usd:.4f}",
        file=sys.stderr,
    )
    for reason, n in report.rejects.most_common():
        print(f"  rejected {n:3d}  {reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
