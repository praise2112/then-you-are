"""The player's own moves at mining positions, each judged once by Flash: judge records on the
moves the judge SLM has to rule on, and preference pairs from the same verdicts.

    uv run python -m arena_evals.datagen.flashmine prefs-1 --player URL --budget 1.0 [--limit 50]

Every call lands in runs/<name>-flash.db, so a rerun pays only for what is new. Writes under
the corpus directory <name>.flash-judge.jsonl (messages and Flash's verdict, the sft.py judge
format), <name>.flash-scored.jsonl (every judged move with its total, prefs.py's KTO input)
and <name>.flash-pairs.jsonl (prefs.py's pair input).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from arena_core.state import weighted_total
from arena_core.template import Template
from arena_evals.common import judge_with_backoff, load_model, make_caller
from arena_evals.datagen.ledger import (
    BudgetReached,
    CallFailed,
    CallRow,
    Ledger,
    Tape,
    judge_call_row,
    move_call_row,
)
from arena_evals.datagen.mine import ScoredMove
from arena_evals.datagen.prefset import JUDGE_RUNS, Position, pair_row, playable, with_move
from arena_evals.datagen.run import CORPUS_DIR, RUNS_DIR, corpus_games
from arena_evals.judge_eval import RANK_GAP
from arena_judge.caller import ModelCaller, ModelSpec
from arena_judge.prompt import clean_move
from arena_judge.schema import JudgeResponse

DRAWS = 4
JUDGE = "judge-v1-direct"


def teacher_turn(corpus: list[Ledger], pos: Position) -> CallRow:
    """The corpus judge call that ruled on the teacher's move at this position."""
    match_id, seq = pos.id.rsplit("/", 1)
    for ledger in corpus:
        for call in ledger.calls(match_id):
            if (
                call.role == "judge"
                and call.seq == int(seq)
                and call.payload["move"] == pos.teacher_move
            ):
                return call
    raise LookupError(f"{pos.id}: no judged teacher move in the corpus")


async def draw_and_judge(
    pos: Position,
    k: int,
    template: Template,
    turn: CallRow,
    ledger: Ledger,
    caller: ModelCaller,
    player: ModelSpec,
    judge: ModelSpec,
    over_budget,
) -> tuple[str, JudgeResponse] | None:
    """One sampled move and Flash's verdict on it, replayed from the ledger when recorded."""
    tape = Tape(ledger, f"{pos.id}/draw{k}", over_budget)
    asked = {"messages": pos.player_messages}
    written = await tape.step(
        "move",
        turn.actor,
        turn.seq,
        lambda idx: move_call_row(
            caller,
            player,
            pos.player_messages,
            tape.key,
            idx,
            turn.actor,
            turn.seq,
            "player",
            asked,
        ),
        asked,
    )
    move = clean_move(written.raw)
    if not playable(template, pos, move):
        return None
    p = turn.payload
    inputs = {
        "previous": p["previous"],
        "move": move,
        "hidden": p["hidden"],
        "transcript": p["transcript"],
    }

    async def live(idx: int) -> CallRow:
        call = await judge_with_backoff(
            caller, template, p["transcript"], p["previous"], move, p["hidden"], judge
        )
        return judge_call_row(tape.key, idx, turn.actor, turn.seq, judge, call, inputs)

    verdict = await tape.step("judge", turn.actor, turn.seq, live, inputs)
    response = verdict.payload["response"]
    return (move, JudgeResponse.model_validate(response)) if response else None


async def main(args) -> None:
    base = CORPUS_DIR / args.name
    positions = [
        Position.model_validate_json(x)
        for x in Path(f"{base}.positions.jsonl").read_text().splitlines()
    ][: args.limit]
    templates = corpus_games(sorted({p.template_id for p in positions}))[0]
    corpus = [Ledger(RUNS_DIR / f"{run}.db") for run in JUDGE_RUNS]
    ledger = Ledger(RUNS_DIR / f"{args.name}-flash.db")
    caller = make_caller(judge_ref=JUDGE)
    judge = load_model(JUDGE)
    player = load_model("student-local").model_copy(
        update={"model": "m", "base_url": args.player, "temperature": 0.9}
    )
    start = ledger.spent()
    sem = asyncio.Semaphore(args.concurrency)
    judged: list[dict] = []
    moves: list[ScoredMove] = []
    pairs: list[dict] = []

    def over_budget() -> bool:
        return ledger.spent() - start >= args.budget

    async def one(pos: Position) -> None:
        template = templates[pos.template_id]
        scored = []
        async with sem:
            turn = teacher_turn(corpus, pos)
            for k in range(DRAWS):
                try:
                    got = await draw_and_judge(
                        pos, k, template, turn, ledger, caller, player, judge, over_budget
                    )
                except (BudgetReached, CallFailed):
                    return
                if got is None:
                    continue
                move, response = got
                messages = with_move(pos.judge_messages, move)
                judged.append({"messages": messages, "response": response.model_dump()})
                total = weighted_total(response.scoring.scores, template.weights)
                passes = all(response.scoring.gates.model_dump().values())
                moves.append(ScoredMove(position=pos.id, move=move, totals=[total], passes=passes))
                if passes:
                    scored.append((total, move))
        scored.sort()
        if len(scored) >= 2 and scored[-1][0] - scored[0][0] > RANK_GAP:
            pairs.append(pair_row(pos, scored[-1][1], scored[0][1], scored[-1][0] - scored[0][0]))

    try:
        await asyncio.gather(*(one(p) for p in positions))
    finally:
        await caller.aclose()
        spent = ledger.spent() - start
        for db in [*corpus, ledger]:
            db.close()
    scored_rows = [m.model_dump() for m in moves]
    for suffix, rows in (
        ("flash-judge", judged),
        ("flash-scored", scored_rows),
        ("flash-pairs", pairs),
    ):
        Path(f"{base}.{suffix}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        )
    print(
        f"{len(positions)} positions, {len(judged)} judged moves, {len(pairs)} pairs, "
        f"${spent:.4f} spent now",
        file=sys.stderr,
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="a positions set under the corpus directory")
    ap.add_argument("--player", required=True, help="the player's OpenAI-compatible base URL")
    ap.add_argument("--budget", type=float, required=True, help="dollars; the run stops here")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=16)
    asyncio.run(main(ap.parse_args()))
