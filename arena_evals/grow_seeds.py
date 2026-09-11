"""Grow a template's seed pool: sample the recipe grid, generate, dedup, append.

uv run python -m arena_evals.grow_seeds --template then-i-am --target 500 [--dry-run]
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
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from arena_core.template import TEMPLATES_DIR, Seed, Template, load_template
from arena_judge.caller import CallError, ModelCaller
from arena_server.config import load_model, load_settings

EMBED_URL = "https://openrouter.ai/api/v1/embeddings"
EMBED_MODEL = "openai/text-embedding-3-small"
EMBED_BATCH = 256
CACHE_DIR = Path(__file__).parent / "seeds"
CANDIDATES_PER_CELL = 6
KEEP_PER_CELL = 3
AVOID_MAX = 150
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
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
    counter: str


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[Candidate]


@dataclass
class Rejects:
    by_reason: Counter[str] = field(default_factory=Counter)

    def add(self, reason: str) -> None:
        self.by_reason[reason] += 1


@dataclass
class Report:
    accepted: list[Seed]
    rejects: Rejects
    calls: int
    cost_usd: float


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


def mechanical_reason(cand: Candidate) -> str | None:
    """Why a candidate fails the cheap checks, or None when it passes them all."""
    text = cand.opening.strip()
    if not text.lower().startswith(ARTICLES):
        return "no article"
    if re.match(r"a [aeio]|an [^aeiouh]", text.lower()):
        return "wrong article"
    if len(text.split()) > 3:
        return "too long"
    last = text.split()[-1].lower()
    if last.endswith("ing") and last not in ING_NOUNS:
        return "dangling verb"
    if text != text.lower() and not re.search(
        r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", text
    ):
        return "capitalised"
    if not is_single_emoji(cand.emoji):
        return "bad emoji"
    if len(cand.counter.split()) < 2:
        return "no counter"
    return None


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def generator_messages(template: Template, cell: dict[str, str], avoid: list[str]) -> list[dict]:
    recipe = template.seed_recipe
    assert recipe is not None
    tests = "\n".join(f"- {t}" for t in recipe.tests)
    cell_text = ", ".join(f"{axis}: {value}" for axis, value in cell.items())
    avoid_text = "; ".join(avoid) if avoid else "(none yet)"
    system = (
        f"You write opening forms for a word game called {template.title}. "
        f"{template.premise.strip()}\n"
        f"Every opening must pass these tests:\n{tests}\n"
        'Reply with JSON only: {"candidates": [{"opening": "a snowman", "emoji": "⛄", '
        '"counter": "the sun"}, ...]}. The emoji is one emoji that shows the opening.'
    )
    user = (
        f"Give {CANDIDATES_PER_CELL} openings from this corner of the world: {cell_text}.\n"
        f"Each one a different thing, none of them a cousin of another. "
        f"At least three are one word after the article, like a wasp. "
        f"Order them by how much a stranger would want to answer, best first.\n"
        f"Already taken, do not repeat or paraphrase: {avoid_text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_batch(raw: str) -> Batch | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return Batch.model_validate_json(match.group(0))
    except ValidationError:
        return None


async def with_backoff(fn, *, tries: int = 6):
    """Retry a call on rate limits, server errors and timeouts, honouring Retry-After."""
    for attempt in range(tries):
        try:
            return await fn()
        except (CallError, httpx.HTTPStatusError, httpx.TransportError) as e:
            status = getattr(e, "status", None) or getattr(
                getattr(e, "response", None), "status_code", None
            )
            if status is not None and status not in RETRY_STATUSES:
                raise
            if attempt == tries - 1:
                raise
            retry_after = getattr(getattr(e, "response", None), "headers", {}).get("Retry-After")
            wait = (
                float(retry_after) if retry_after else min(30.0, 0.5 * 2**attempt) + random.random()
            )
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")


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
                    max_tokens=600,
                    reasoning={"enabled": False},
                )
            )
        self.calls += 1
        self.cost += result.cost_usd
        batch = parse_batch(result.text)
        return batch.candidates[:KEEP_PER_CELL] if batch else []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async def post(chunk: list[str]) -> httpx.Response:
            resp = await self.caller.client.post(
                EMBED_URL, json={"model": EMBED_MODEL, "input": chunk}
            )
            resp.raise_for_status()
            return resp

        async def one(chunk: list[str]) -> list[list[float]]:
            async with self.sem:
                resp = await with_backoff(lambda: post(chunk))
            return [row["embedding"] for row in resp.json()["data"]]

        chunks = [texts[i : i + EMBED_BATCH] for i in range(0, len(texts), EMBED_BATCH)]
        rows = await asyncio.gather(*(one(c) for c in chunks))
        self.calls += len(chunks)
        return list(itertools.chain.from_iterable(rows))

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

    def nearest(self, vec: list[float], others: dict[str, list[float]]) -> tuple[str, float]:
        best = ("", -1.0)
        for token, other in others.items():
            score = cosine(vec, other)
            if score > best[1]:
                best = (token, score)
        return best

    async def round(self, cells: list[dict[str, str]], rejects: Rejects) -> list[Seed]:
        pool = self.template.seed_pool
        tokens = [s.opening_token for s in pool]
        avoid = tokens if len(tokens) <= AVOID_MAX else random.sample(tokens, AVOID_MAX)
        raw = await asyncio.gather(*(self.generate_cell(c, avoid) for c in cells))
        heads = {head_noun(s.opening_token) for s in pool}
        seen_tokens = {s.opening_token.lower() for s in pool}
        survivors: list[Candidate] = []
        for cand in itertools.chain.from_iterable(raw):
            cand.opening = cand.opening.strip().rstrip(".")
            if (reason := mechanical_reason(cand)) is not None:
                rejects.add(reason)
                continue
            if cand.opening.lower() in seen_tokens:
                rejects.add("exact repeat")
                continue
            head = head_noun(cand.opening)
            if head in heads:
                rejects.add("same head noun")
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
            token, score = self.nearest(vec, known)
            if score >= self.threshold:
                rejects.add(f"too close ({token})")
                continue
            known[cand.opening] = vec
            accepted.append(Seed(opening_token=cand.opening, opening_emoji=cand.emoji))
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


def append_to_pool(template: Template, seeds: list[Seed]) -> Path:
    """Add seeds as flow-style lines after the last pool entry, keeping the file's own layout."""
    path = TEMPLATES_DIR / template.slug / "v1.yaml"
    text = path.read_text()
    last = template.seed_pool[-1]
    anchor = (
        f'  - {{ opening_token: "{last.opening_token}", opening_emoji: "{last.opening_emoji}" }}\n'
    )
    if text.count(anchor) != 1:
        raise ValueError(f"could not find the last seed line in {path}")
    lines = "".join(
        f'  - {{ opening_token: "{s.opening_token}", opening_emoji: "{s.opening_emoji}" }}\n'
        for s in seeds
    )
    path.write_text(text.replace(anchor, anchor + lines))
    return path


async def grow(
    template_id: str, target: int, concurrency: int, threshold: float, dry_run: bool
) -> Report:
    template = load_template(template_id)
    if template.seed_recipe is None:
        raise SystemExit(f"{template_id} has no seed_recipe")
    settings = load_settings()
    caller = ModelCaller(
        settings.openrouter_api_key,
        load_model(settings.judge_ref),
        load_model(settings.opponent_ref),
    )
    grower = Grower(template, caller, concurrency, threshold)
    rejects = Rejects()
    accepted: list[Seed] = []
    try:
        await grower.load_pool_vectors()
        cells = all_cells(template)
        probe = await grower.generate_cell(cells[0], [])
        if not probe:
            raise SystemExit("the generator returned nothing parsable on a probe call")
        while len(accepted) < target and cells:
            shortfall = target - len(accepted)
            # Half the candidates fall to dedup once the pool has some size; ask for twice the need.
            take = min(len(cells), max(1, math.ceil(2 * shortfall / KEEP_PER_CELL)))
            batch, cells = cells[:take], cells[take:]
            got = await grower.round(batch, rejects)
            if not got:
                print("a whole round yielded nothing, stopping", file=sys.stderr)
                break
            accepted.extend(got[: target - len(accepted)])
            template = template.model_copy(update={"seed_pool": [*template.seed_pool, *got]})
            grower.template = template
            print(
                f"round: {len(batch)} cells, +{len(got)}, total {len(accepted)}/{target}",
                file=sys.stderr,
            )
    finally:
        await caller.aclose()
    if accepted and not dry_run:
        append_to_pool(load_template(template_id), accepted)
        load_template(template_id)  # the file must still lint
    return Report(accepted, rejects, grower.calls, grower.cost)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=64)
    ap.add_argument(
        "--threshold", type=float, default=0.65, help="cosine above this is a duplicate"
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(
        grow(args.template, args.target, args.concurrency, args.threshold, args.dry_run)
    )
    for seed in report.accepted:
        print(f"{seed.opening_emoji} {seed.opening_token}")
    one_word = sum(len(s.opening_token.split()) == 2 for s in report.accepted)
    print(
        f"\naccepted {len(report.accepted)} ({one_word} one word), "
        f"calls {report.calls}, cost ${report.cost_usd:.4f}",
        file=sys.stderr,
    )
    for reason, n in report.rejects.by_reason.most_common():
        print(f"  rejected {n:3d}  {reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
