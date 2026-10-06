"""GRPO with LoRA on a trained player, rewarded by Flash's verdict on every sampled move.

    uv run --with trl==1.14.0 --with peft python -m arena_train.grpo rl-1 \\
        --model ~/oddstage-runs/prefs-qwen35-08b-ipo-sft-r2-s0/model --positions 1500 --budget 9

Positions come from the mined training positions whose earlier judged draws disagreed. Every
Flash call lands in runs/<name>.db, so a rerun pays only for moves not judged yet, and training
resumes from its last checkpoint. The merged model is written to --out/<name>/model.
"""

import argparse
import asyncio
import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from arena_core.template import Template
from arena_evals.common import load_model, make_caller, read_jsonl
from arena_evals.datagen.flashmine import teacher_turn
from arena_evals.datagen.ledger import Budget, CallFailed, JudgeInputs, Ledger, Tape
from arena_evals.datagen.prefset import JUDGE_RUNS, Position, playable
from arena_evals.datagen.run import CORPUS_DIR, RUNS_DIR, corpus_games
from arena_evals.judge_eval import RANK_GAP
from arena_evals.train_eval import Scored, verdict_score
from arena_judge.caller import ModelCaller, ModelSpec
from arena_judge.prompt import clean_move
from arena_judge.schema import SCORE_MAX

JUDGE = "judge-v1-direct"
MINED = ("prefs-1", "prefs-2")
QUALITY = 0.5
CONCURRENCY = 64


@dataclass(frozen=True)
class Spot:
    """A training position with what the judge needs to rule on a new move there."""

    position: Position
    template: Template
    actor: str
    seq: int
    judge_inputs: dict


def informative(scored: list[dict]) -> set[str]:
    """Positions whose earlier draws disagreed: some passed and some failed, or all passed
    with totals further apart than RANK_GAP."""
    draws = defaultdict(list)
    for row in scored:
        draws[row["position"]].append(row)
    keep = set()
    for position, rows in draws.items():
        passed = [row["totals"][0] for row in rows if row["passes"]]
        mixed = 0 < len(passed) < len(rows)
        spread = len(passed) >= 2 and max(passed) - min(passed) > RANK_GAP
        if mixed or spread:
            keep.add(position)
    return keep


def reward_of(scored: Scored, template: Template) -> float:
    """1 for a move that stands, plus up to QUALITY for its weighted total."""
    best = SCORE_MAX * sum(template.weights.values())
    return float(scored.stood) + QUALITY * scored.total / best


async def judge_move(
    spot: Spot, move: str, ledger: Ledger, caller: ModelCaller, spec: ModelSpec, over_budget
) -> Scored:
    """The engine's check, then Flash's verdict, replayed from the ledger when recorded."""
    if not playable(spot.template, spot.position, move):
        return Scored(text=move, outcome="refused", total=0)
    digest = hashlib.sha1(move.encode()).hexdigest()[:16]
    tape = Tape(ledger, f"{spot.position.id}/{digest}", over_budget)
    ask = JudgeInputs(move=move, **spot.judge_inputs)
    response = await tape.judge(caller, spec, spot.template, spot.actor, spot.seq, ask)
    return verdict_score(spot.template, move, response)


def flash_reward(spots: dict[str, Spot], run: str, budget: float):
    """TRL's async reward function. The ledger and the HTTP client open on first use, on the
    trainer's event-loop thread, where every later call also runs."""
    state: dict = {}
    spec = load_model(JUDGE)

    async def flash(prompts, completions, position, log_metric, **_):
        if not state:
            state["ledger"] = Ledger(RUNS_DIR / f"{run}.db")
            state["budget"] = Budget(state["ledger"], budget)
            state["caller"] = make_caller(judge_ref=JUDGE)
            state["sem"] = asyncio.Semaphore(CONCURRENCY)
        ledger = state["ledger"]

        async def one(position_id: str, completion: list[dict]) -> Scored | None:
            spot = spots[position_id]
            async with state["sem"]:
                try:
                    return await judge_move(
                        spot,
                        clean_move(completion[0]["content"]),
                        ledger,
                        state["caller"],
                        spec,
                        state["budget"].over,
                    )
                except CallFailed:
                    return None

        results = await asyncio.gather(
            *(one(p, c) for p, c in zip(position, completions, strict=True))
        )
        done = [r for r in results if r is not None]
        log_metric("stood", sum(r.stood for r in done) / max(1, len(done)))
        log_metric("refused", sum(r.outcome == "refused" for r in done) / max(1, len(done)))
        log_metric("spent", state["budget"].spent())
        return [
            None if r is None else reward_of(r, spots[p].template)
            for p, r in zip(position, results, strict=True)
        ]

    return flash


def load_spots(count: int, seed: int) -> dict[str, Spot]:
    positions = [
        Position.model_validate(r)
        for name in MINED
        for r in read_jsonl(CORPUS_DIR / f"{name}.positions.jsonl")
    ]
    scored = [r for name in MINED for r in read_jsonl(CORPUS_DIR / f"{name}.flash-scored.jsonl")]
    keep = informative(scored)
    chosen = [p for p in positions if p.id in keep]
    random.Random(seed).shuffle(chosen)
    chosen = chosen[:count]
    templates = corpus_games(sorted({p.template_id for p in chosen}))[0]
    corpus = [Ledger(RUNS_DIR / f"{run}.db") for run in JUDGE_RUNS]
    spots = {}
    try:
        for p in chosen:
            turn = teacher_turn(corpus, p)
            inputs = {k: turn.payload[k] for k in ("previous", "hidden", "transcript")}
            spots[p.id] = Spot(p, templates[p.template_id], turn.actor, turn.seq, inputs)
    finally:
        for db in corpus:
            db.close()
    return spots


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="the run's name, for its ledger and output folder")
    ap.add_argument("--model", type=Path, required=True, help="the player's model folder")
    ap.add_argument("--positions", type=int, required=True)
    ap.add_argument("--budget", type=float, required=True, help="dollars; the run stops here")
    ap.add_argument("--out", type=Path, default=Path.home() / "oddstage-runs")
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import GRPOConfig, GRPOTrainer

    spots = load_spots(args.positions, args.seed)
    print(f"{len(spots)} positions", flush=True)
    out = args.out / args.name
    dataset = Dataset.from_list(
        [{"prompt": s.position.player_messages, "position": pid} for pid, s in spots.items()]
    )
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32, device_map="cuda")
    config = GRPOConfig(
        output_dir=str(out / "trainer"),
        learning_rate=args.lr,
        num_train_epochs=1,
        num_generations=8,
        per_device_train_batch_size=16,
        gradient_accumulation_steps=8,
        steps_per_generation=8,
        max_completion_length=96,
        temperature=1.0,
        scale_rewards="none",
        epsilon_high=0.28,
        beta=0.0,
        bf16=True,
        gradient_checkpointing=True,
        chat_template_kwargs={"enable_thinking": False},
        logging_steps=1,
        save_steps=10,
        save_total_limit=2,
        report_to="none",
        seed=args.seed,
    )
    trainer = GRPOTrainer(
        model=model,
        reward_funcs=flash_reward(spots, args.name, args.budget),
        args=config,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=LoraConfig(
            r=16, lora_alpha=32, target_modules="all-linear", task_type="CAUSAL_LM"
        ),
    )
    resume = any((out / "trainer").glob("checkpoint-*"))
    trainer.train(resume_from_checkpoint=resume or None)
    (out / "log.json").write_text(json.dumps(trainer.state.log_history, indent=1))
    merged = trainer.model.merge_and_unload()
    merged.save_pretrained(out / "model")
    tokenizer.save_pretrained(out / "model")


if __name__ == "__main__":
    main()
