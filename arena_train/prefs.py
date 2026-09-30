# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "torch==2.14.0",
#     "transformers==5.17.0",
#     "trl==1.14.0",
#     "accelerate==1.15.0",
#     "datasets==5.0.1",
#     "einops==0.8.2",
#     "kernels==0.16.2",
#     "b2sdk==2.13.0",
#     "mlflow-skinny==3.16.1",
# ]
# ///
"""Preference training of an SFT player on mined pairs, then its answers at the eval contexts.

    uv run arena_train/prefs.py --method dpo --model retrain-qwen35-08b-s0 \\
        --pairs pairs.jsonl --scored scored.jsonl --contexts contexts.jsonl --out out [--run NAME]

--model names an SFT run in B2 (its model/ folder is downloaded) or a local folder. DPO, IPO
and SimPO train on the pairs; KTO trains on every scored move, labelled by its judge scores.
"""

import argparse
import contextlib
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import mlflow
import torch
from datasets import Dataset
from sft import answer_contexts, load_model, thinking_switch, upload
from transformers import AutoTokenizer
from trl import DPOConfig, DPOTrainer, KTOConfig, KTOTrainer
from trl.experimental.cpo import CPOConfig, CPOTrainer

METHODS = {
    "dpo": {"loss_type": ["sigmoid_norm"], "beta": 5.0, "lr": 8e-7},
    "ipo": {"loss_type": ["ipo"], "beta": 0.1, "lr": 8e-7},
    "simpo": {"loss_type": "simpo", "beta": 2.0, "simpo_gamma": 1.0, "lr": 1e-6},
    "kto": {"beta": 0.1, "lr": 1e-6},
}


def download(run: str, dest: Path) -> Path:
    from b2sdk.v2 import B2Api, InMemoryAccountInfo

    api = B2Api(InMemoryAccountInfo())
    api.authorize_account(
        "production", os.environ["B2_APP_KEY_ID_PERSONAL"], os.environ["B2_APP_KEY_PERSONAL"]
    )
    bucket = api.get_bucket_by_name(os.environ["B2_BUCKET_NAME_PERSONAL"])
    prefix = f"oddstage/runs/{run}/model/"
    for fv, _ in bucket.ls(prefix, recursive=True):
        target = dest / fv.file_name.removeprefix(prefix)
        target.parent.mkdir(parents=True, exist_ok=True)
        bucket.download_file_by_name(fv.file_name).save_to(str(target))
    return dest


def render(tokenizer, messages: list[dict], move: str, chat_kwargs: dict) -> tuple[str, str]:
    """The prompt as the served chat template renders it, and the move as its continuation."""
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, **chat_kwargs
    )
    full = tokenizer.apply_chat_template(
        [*messages, {"role": "assistant", "content": move}], tokenize=False, **chat_kwargs
    )
    if not full.startswith(prompt):
        raise SystemExit("the prompt renders differently inside the full chat")
    return prompt, full[len(prompt) :]


def pair_rows(tokenizer, pairs: list[dict], chat_kwargs: dict) -> list[dict]:
    rows = []
    for p in pairs:
        prompt, chosen = render(tokenizer, p["messages"], p["chosen"], chat_kwargs)
        rejected = render(tokenizer, p["messages"], p["rejected"], chat_kwargs)[1]
        rows.append({"prompt": prompt, "chosen": chosen, "rejected": rejected})
    return rows


def kto_rows(tokenizer, scored: list[dict], messages: dict, chat_kwargs: dict) -> list[dict]:
    """A move is desirable when it passed the gates and its mean total beats the mean of the
    judged moves at its position; the rest are undesirable."""
    by_position = defaultdict(list)
    for s in scored:
        if s["totals"] and s["position"] in messages:
            by_position[s["position"]].append(s)
    rows = []
    for position, moves in by_position.items():
        if len(moves) < 2:
            continue
        mean = statistics.mean(statistics.mean(m["totals"]) for m in moves)
        for m in moves:
            prompt, completion = render(tokenizer, messages[position], m["move"], chat_kwargs)
            good = m["passes"] and statistics.mean(m["totals"]) > mean
            rows.append({"prompt": prompt, "completion": completion, "label": good})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=list(METHODS), required=True)
    ap.add_argument("--model", required=True, help="an SFT run name in B2, or a local folder")
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--scored", type=Path, help="every scored move; KTO trains on these")
    ap.add_argument("--contexts", type=Path, help="eval contexts to answer after training")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--run", help="upload --out to B2 under this name")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--micro-batch", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--track", action="store_true", help="log the run to MLflow")
    args = ap.parse_args()
    if args.method == "kto" and not args.scored:
        ap.error("kto trains on --scored")
    args.out.mkdir(parents=True, exist_ok=True)
    local = Path(args.model)
    source = local if local.is_dir() else download(args.model, args.out.with_name("sft-model"))

    tokenizer = AutoTokenizer.from_pretrained(source)
    chat_kwargs = thinking_switch(tokenizer)
    pairs = [json.loads(x) for x in args.pairs.read_text().splitlines()]
    if args.method == "kto":
        messages = {p["id"]: p["messages"] for p in pairs}
        scored = [json.loads(x) for x in args.scored.read_text().splitlines()]
        rows = kto_rows(tokenizer, scored, messages, chat_kwargs)
    else:
        rows = pair_rows(tokenizer, pairs, chat_kwargs)
    model = load_model(source)

    spec = dict(METHODS[args.method])
    lr = spec.pop("lr")
    common = {
        "output_dir": str(args.out / "trainer"),
        "num_train_epochs": args.epochs,
        "learning_rate": lr,
        "lr_scheduler_type": "cosine_with_min_lr",
        "lr_scheduler_kwargs": {"min_lr": lr / 10},
        "warmup_steps": 0.03,
        "per_device_train_batch_size": args.micro_batch,
        "gradient_accumulation_steps": args.batch // args.micro_batch,
        "gradient_checkpointing": False,
        "bf16": True,
        "max_length": 2048,
        "seed": args.seed,
        "logging_steps": 5,
        "save_strategy": "no",
        "report_to": "mlflow" if args.track else "none",
    }
    if args.method in ("dpo", "ipo"):
        config = DPOConfig(**common, **spec, precompute_ref_log_probs=True)
        trainer_cls = DPOTrainer
    elif args.method == "simpo":
        config = CPOConfig(**common, **spec, cpo_alpha=0.0)
        trainer_cls = CPOTrainer
    else:
        good = sum(r["label"] for r in rows)
        # KTO wants the weighted desirable and undesirable counts roughly balanced.
        config = KTOConfig(**common, **spec, undesirable_weight=good / max(1, len(rows) - good))
        trainer_cls = KTOTrainer

    name = args.run or f"prefs-{args.method}"
    if args.track:
        mlflow.set_experiment("oddstage-training")
    with mlflow.start_run(run_name=name) if args.track else contextlib.nullcontext():
        trainer = trainer_cls(
            model=model,
            args=config,
            train_dataset=Dataset.from_list(rows),
            processing_class=tokenizer,
        )
        began = time.monotonic()
        trainer.train()
        metrics = {
            "method": args.method,
            "model": args.model,
            "rows": len(rows),
            "seconds": round(time.monotonic() - began, 1),
            "peak_memory_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
            "log": trainer.state.log_history,
        }
        (args.out / "metrics.json").write_text(json.dumps(metrics, indent=1))
        print(json.dumps({k: v for k, v in metrics.items() if k != "log"}), file=sys.stderr)
    trainer.save_model(str(args.out / "model"))
    tokenizer.save_pretrained(str(args.out / "model"))
    if args.contexts:
        answer_contexts(model, tokenizer, args.contexts, args.out, 1.0, 96)
    if args.run:
        upload(args.out, args.run)


if __name__ == "__main__":
    main()
