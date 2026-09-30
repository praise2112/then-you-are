"""Preference pairs from the player's own moves, scored by the judge SLM, both on
OpenAI-compatible servers (vLLM). Four moves a position, five judge scores a move.

    uv run python -m arena_evals.datagen.mine prefs-1 --player URL --judge URL [--limit 100]

Writes <name>.scored.jsonl (every judged move, for KTO) and <name>.pairs.jsonl (chosen and
rejected where the margin clears twice the judge's noise). A rerun skips positions already
scored.
"""

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel

from arena_core.state import normalize, weighted_total
from arena_core.template import Template
from arena_evals.datagen.prefset import CORPUS, Position, with_move
from arena_evals.datagen.run import corpus_games
from arena_judge.prompt import clean_move

MOVES = 4
SCORES = 5
NO_THINKING = {"enable_thinking": False}


class ScoredMove(BaseModel):
    position: str
    move: str
    totals: list[int]
    passes: bool

    @property
    def mean(self) -> float:
        return statistics.mean(self.totals)

    @property
    def noise(self) -> float:
        return statistics.pstdev(self.totals)


def read_ruling(text: str) -> tuple[dict, dict] | None:
    """The gates and scores objects from a judge reply, which may stop after them."""
    found = []
    for key in ('"gates"', '"scores"'):
        at = text.find(key)
        start = text.find("{", at) if at >= 0 else -1
        if start < 0:
            return None
        try:
            found.append(json.JSONDecoder().raw_decode(text, start)[0])
        except json.JSONDecodeError:
            return None
    return found[0], found[1]


def select_pair(moves: list[ScoredMove]) -> tuple[ScoredMove, ScoredMove] | None:
    """Best and worst of the moves that passed the gates, if their gap in mean totals is at
    least twice the mean of their two noises and above zero."""
    passed = sorted((m for m in moves if m.passes), key=lambda m: m.mean)
    if len(passed) < 2:
        return None
    worst, best = passed[0], passed[-1]
    margin = best.mean - worst.mean
    return (best, worst) if margin > 0 and margin >= best.noise + worst.noise else None


async def chat(client: httpx.AsyncClient, url: str, messages: list, **params) -> list[str]:
    body = {"model": "m", "messages": messages, "chat_template_kwargs": NO_THINKING, **params}
    resp = await client.post(f"{url.rstrip('/')}/chat/completions", json=body)
    resp.raise_for_status()
    return [c["message"]["content"] or "" for c in resp.json()["choices"]]


async def score_position(
    client: httpx.AsyncClient, args, pos: Position, template: Template
) -> list[ScoredMove]:
    drafts = await chat(
        client, args.player, pos.player_messages, n=MOVES, temperature=0.9, max_tokens=96
    )
    taken = {normalize(t) for t in pos.on_table}
    moves = [
        m
        for m in dict.fromkeys(clean_move(d) for d in drafts)
        if normalize(m)
        and len(m) <= template.move_constraints.max_chars
        and normalize(m) not in taken
    ]
    scored = []
    for move in moves:
        replies = await chat(
            client,
            args.judge,
            with_move(pos.judge_messages, move),
            n=SCORES,
            temperature=0.7,
            max_tokens=args.judge_tokens,
        )
        rulings = [r for r in map(read_ruling, replies) if r is not None]
        if len(rulings) < 3:
            continue
        totals = [weighted_total(scores, template.weights) for _, scores in rulings]
        passes = sum(all(gates.values()) for gates, _ in rulings) > len(rulings) / 2
        scored.append(ScoredMove(position=pos.id, move=move, totals=totals, passes=passes))
    return scored


async def main(args) -> None:
    base = Path(CORPUS) / args.name
    lines = Path(f"{base}.positions.jsonl").read_text().splitlines()
    positions = [Position.model_validate_json(x) for x in lines]
    scored_path, pairs_path = Path(f"{base}.scored.jsonl"), Path(f"{base}.pairs.jsonl")
    done = set()
    if scored_path.exists():
        done = {json.loads(x)["position"] for x in scored_path.read_text().splitlines()}
    todo = [p for p in positions[: args.limit] if p.id not in done]
    templates = corpus_games(sorted({p.template_id for p in todo}))[0]
    sem = asyncio.Semaphore(args.concurrency)
    kept = 0
    async with httpx.AsyncClient(timeout=600) as client:

        async def one(pos: Position) -> None:
            nonlocal kept
            async with sem:
                moves = await score_position(client, args, pos, templates[pos.template_id])
            with scored_path.open("a") as f:
                # A position with no judged move still gets a line, so a rerun skips it.
                blank = ScoredMove(position=pos.id, move="", totals=[], passes=False)
                f.writelines(m.model_dump_json() + "\n" for m in moves or [blank])
            pair = select_pair(moves)
            if pair:
                kept += 1
                best, worst = pair
                row = {
                    "id": pos.id,
                    "template_id": pos.template_id,
                    "messages": pos.player_messages,
                    "chosen": best.move,
                    "rejected": worst.move,
                    "margin": best.mean - worst.mean,
                }
                with pairs_path.open("a") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

        await asyncio.gather(*(one(p) for p in todo))
    print(f"{len(todo)} positions scored, {kept} pairs kept", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="a positions set under the corpus directory")
    ap.add_argument("--player", required=True, help="the player's OpenAI-compatible base URL")
    ap.add_argument("--judge", required=True, help="the judge SLM's base URL")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--judge-tokens", type=int, default=160, help="enough to reach the scores")
    asyncio.run(main(ap.parse_args()))
