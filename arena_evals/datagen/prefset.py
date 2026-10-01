"""Mining positions for preference training: player records, each joined to the judge
conversation that ruled on the teacher's move there, so a new move can be judged in its place.

    uv run python -m arena_evals.datagen.prefset train-2 --count 4000 --out prefs-1
"""

import argparse
import random
import sys

from pydantic import BaseModel

from arena_core.state import normalize
from arena_core.template import Template
from arena_evals.datagen.records import JudgeRecord, PlayerRecord
from arena_evals.datagen.run import CORPUS_DIR

JUDGE_RUNS = ("corpus-1a", "corpus-1b", "corpus-2")


class Position(BaseModel):
    id: str
    template_id: str
    player_messages: list[dict[str, str]]
    judge_messages: list[dict[str, str]]
    teacher_move: str
    on_table: list[str]


def judged_move(judge: JudgeRecord) -> str:
    """The move in a judge conversation's last message."""
    return judge.messages[-1]["content"].rsplit("<move>\n", 1)[1].removesuffix("\n</move>")


def with_move(judge_messages: list[dict[str, str]], move: str) -> list[dict[str, str]]:
    """The same conversation with `move` under judgment instead."""
    head = judge_messages[-1]["content"].rsplit("<move>\n", 1)[0]
    return [*judge_messages[:-1], {"role": "user", "content": f"{head}<move>\n{move}\n</move>"}]


def playable(template: Template, pos: Position, move: str) -> bool:
    """Not empty, under the character cap, and not a repeat of a text already on the table."""
    taken = {normalize(t) for t in pos.on_table}
    return (
        bool(normalize(move))
        and len(move) <= template.move_constraints.max_chars
        and (normalize(move) not in taken)
    )


def pair_row(pos: Position, chosen: str, rejected: str, margin: float) -> dict:
    """One preference pair as prefs.py reads it."""
    return {
        "id": pos.id,
        "template_id": pos.template_id,
        "messages": pos.player_messages,
        "chosen": chosen,
        "rejected": rejected,
        "margin": margin,
    }


def join(players: list[PlayerRecord], judges: list[JudgeRecord]) -> list[Position]:
    """Each player record that the teacher wrote as its best move, with its judge turn."""
    by_turn = {(j.match_id, j.seq, judged_move(j)): j for j in judges if j.response}
    found = []
    for p in players:
        prov = p.provenance
        judge = by_turn.get((prov.match_id, prov.seq, p.target))
        if prov.target_quality != "best" or judge is None:
            continue
        found.append(
            Position(
                id=f"{prov.match_id}/{prov.seq}",
                template_id=prov.template_id,
                player_messages=p.messages,
                judge_messages=judge.messages,
                teacher_move=p.target,
                on_table=[prov.card, *(line.split(": ", 1)[1] for line in prov.transcript)],
            )
        )
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("players", help="a training set under the corpus directory")
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--out", required=True, help="a new name under the corpus directory")
    args = ap.parse_args()
    with open(CORPUS_DIR / args.players / "player.jsonl") as f:
        players = [PlayerRecord.model_validate_json(x) for x in f]
    judges = []
    for run in JUDGE_RUNS:
        with open(CORPUS_DIR / run / "judge.jsonl") as f:
            judges += [JudgeRecord.model_validate_json(x) for x in f]
    positions = join(players, judges)
    picked = random.Random(0).sample(positions, min(args.count, len(positions)))
    with open(CORPUS_DIR / f"{args.out}.positions.jsonl", "x") as f:
        f.writelines(p.model_dump_json() + "\n" for p in picked)
    print(f"{len(picked)} of {len(positions)} joined positions", file=sys.stderr)


if __name__ == "__main__":
    main()
