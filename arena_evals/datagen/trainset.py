"""The player training set: every run's player records, each game but Then I Am cut to 5%.

    uv run python -m arena_evals.datagen.trainset corpus-1a corpus-1b corpus-2 --out train
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

CORPUS = Path(__file__).parent / "corpus"
UNCAPPED = "then-i-am"
MAX_SHARE = 0.05


def cap_shares(by_game: dict[str, list[str]], rng: random.Random) -> dict[str, list[str]]:
    """Drops random records from each game over MAX_SHARE of the total until none is over.
    The result depends on the records and the rng, not on the order they came in."""
    by_game = {g: sorted(v) for g, v in sorted(by_game.items())}
    while True:
        total = sum(len(v) for v in by_game.values())
        over = {
            g: len(v) - int(MAX_SHARE * total)
            for g, v in by_game.items()
            if g != UNCAPPED and len(v) > MAX_SHARE * total
        }
        if not over:
            return by_game
        for game, excess in over.items():
            rng.shuffle(by_game[game])
            del by_game[game][:excess]


def half(by_game: dict[str, list[str]], rng: random.Random) -> list[str]:
    """Half of each game's records, so the smaller set keeps the same mix."""
    return [line for v in by_game.values() for line in rng.sample(v, len(v) // 2)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="run ids under the corpus directory")
    ap.add_argument("--out", required=True, help="a new run id for the training set")
    args = ap.parse_args()
    by_game: dict[str, list[str]] = defaultdict(list)
    for run in args.runs:
        for line in (CORPUS / run / "player.jsonl").read_text().splitlines():
            by_game[json.loads(line)["provenance"]["template_id"]].append(line)
    rng = random.Random(0)
    by_game = cap_shares(dict(by_game), rng)
    out = CORPUS / args.out
    out.mkdir()
    full = [line for v in by_game.values() for line in v]
    (out / "player.jsonl").write_text("\n".join(full) + "\n")
    (out / "player-half.jsonl").write_text("\n".join(half(by_game, rng)) + "\n")
    shares = sorted(((len(v) / len(full), g) for g, v in by_game.items()), reverse=True)
    print(f"{len(full)} records; largest shares:", file=sys.stderr)
    for share, game in shares[:3]:
        print(f"  {game} {share:.1%}", file=sys.stderr)


if __name__ == "__main__":
    main()
