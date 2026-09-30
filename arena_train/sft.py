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
"""Full-parameter SFT of one base on Oddstage records, then its answers at the eval contexts.

    uv run arena_train/sft.py --base Qwen/Qwen3-0.6B --records player.jsonl \\
        --contexts contexts.jsonl --out out [--run NAME] [--max-steps 30]
    uv run arena_train/sft.py --base Qwen/Qwen3-0.6B --records judge.jsonl --judge --out out

Player records train on the move, judge records on the verdict JSON with the ruling's fields
first (--evidence-first keeps the judge's own order). With --run and the B2 keys in the
environment, everything in --out is uploaded to oddstage/runs/NAME/ in the bucket. With
--track, the run is logged live to the MLflow server in MLFLOW_TRACKING_URI, in the
oddstage-training experiment. Test runs leave it off.
"""

import argparse
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import mlflow
import torch
from datasets import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.integrations.hub_kernels import get_kernel_mapping_transformers
from trl import SFTConfig, SFTTrainer

NO_THINKING = {"enable_thinking": False}
RULING_FIRST = ("gates", "confidence", "verdict", "truth_proximity", "scores", "evidence")
FALLBACK = "falling back to its reference PyTorch implementation"
RUN_METRICS = ("seconds", "tokens_per_second", "peak_memory_gb")


def thinking_switch(tokenizer) -> dict:
    """The no-thinking template argument, or none for a template that adds its empty think
    block only to the generation prompt, where the prompt would stop being a prefix."""
    prompt = [{"role": "user", "content": "x"}]
    full = tokenizer.apply_chat_template(
        prompt + [{"role": "assistant", "content": "y"}], tokenize=False, **NO_THINKING
    )
    alone = tokenizer.apply_chat_template(
        prompt, tokenize=False, add_generation_prompt=True, **NO_THINKING
    )
    return NO_THINKING if full.startswith(alone) else {}


def player_example(record: dict, chat_kwargs: dict) -> dict:
    return {
        "prompt": record["messages"],
        "completion": [{"role": "assistant", "content": record["target"]}],
        "chat_template_kwargs": chat_kwargs,
    }


def judge_example(record: dict, ruling_first: bool, chat_kwargs: dict) -> dict:
    response = record["response"]
    if ruling_first:
        scoring = response["scoring"]
        response = {**response, "scoring": {k: scoring[k] for k in RULING_FIRST if k in scoring}}
    return {
        "prompt": [{"role": "user", "content": record["prompt"]}],
        "completion": [{"role": "assistant", "content": json.dumps(response, ensure_ascii=False)}],
        "chat_template_kwargs": chat_kwargs,
    }


def load_examples(path: Path, judge: bool, ruling_first: bool, chat_kwargs: dict) -> list[dict]:
    records = [json.loads(line) for line in path.read_text().splitlines()]
    if judge:
        return [judge_example(r, ruling_first, chat_kwargs) for r in records if r["response"]]
    return [player_example(r, chat_kwargs) for r in records]


def check_lengths(tokenizer, examples: list[dict], max_length: int) -> list[int]:
    """Token lengths of every example. Exits when one exceeds `max_length`, since truncation
    would cut the completion, or when the template renders the prompt differently on its own."""
    lengths = []
    for i, ex in enumerate(examples):
        full = tokenizer.apply_chat_template(
            ex["prompt"] + ex["completion"], tokenize=True, **ex["chat_template_kwargs"]
        )["input_ids"]
        prompt = tokenizer.apply_chat_template(
            ex["prompt"], tokenize=True, add_generation_prompt=True, **ex["chat_template_kwargs"]
        )["input_ids"]
        if full[: len(prompt)] != prompt:
            raise SystemExit(f"example {i}: the prompt renders differently inside the full chat")
        lengths.append(len(full))
    if (longest := max(lengths)) > max_length:
        over = sum(n > max_length for n in lengths)
        raise SystemExit(f"{over} examples exceed {max_length} tokens (longest {longest})")
    return lengths


class FallbackWatch(logging.Handler):
    def __init__(self):
        super().__init__()
        self.seen: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if FALLBACK in record.getMessage():
            self.seen.append(record.getMessage())


def check_kernels(model, tokenizer, example: dict) -> None:
    """One forward and backward pass; exits if any layer ran a slow reference implementation."""
    watch = FallbackWatch()
    logging.getLogger("transformers").addHandler(watch)
    batch = tokenizer.apply_chat_template(
        example["prompt"] + example["completion"],
        return_tensors="pt",
        return_dict=True,
        **example["chat_template_kwargs"],
    ).to(model.device)
    with torch.autocast("cuda", dtype=torch.bfloat16):
        loss = model(**batch, labels=batch["input_ids"]).loss
    loss.backward()
    model.zero_grad(set_to_none=True)
    logging.getLogger("transformers").removeHandler(watch)
    if watch.seen:
        raise SystemExit("slow kernel path: " + "; ".join(watch.seen))


def answer_contexts(model, tokenizer, contexts: Path, out: Path, batch: int = 16) -> None:
    """One sampled answer per context at the opponent temperature, as it would be served."""
    rows = [json.loads(line) for line in contexts.read_text().splitlines()]
    chat_kwargs = thinking_switch(tokenizer)
    model.eval()
    model.config.use_cache = True
    tokenizer.padding_side = "left"
    with (out / "answers.jsonl").open("w") as f:
        for start in range(0, len(rows), batch):
            chunk = rows[start : start + batch]
            prompts = [
                tokenizer.apply_chat_template(
                    c["messages"], tokenize=False, add_generation_prompt=True, **chat_kwargs
                )
                for c in chunk
            ]
            enc = tokenizer(prompts, return_tensors="pt", padding=True).to(model.device)
            with torch.no_grad():
                gen = model.generate(
                    **enc, do_sample=True, temperature=1.0, top_p=1.0, max_new_tokens=96
                )
            for c, ids in zip(chunk, gen[:, enc["input_ids"].shape[1] :], strict=True):
                text = tokenizer.decode(ids, skip_special_tokens=True).strip()
                f.write(json.dumps({"context": c["id"], "text": text}, ensure_ascii=False) + "\n")


def upload(out: Path, run: str) -> None:
    from b2sdk.v2 import B2Api, InMemoryAccountInfo

    api = B2Api(InMemoryAccountInfo())
    api.authorize_account(
        "production", os.environ["B2_APP_KEY_ID_PERSONAL"], os.environ["B2_APP_KEY_PERSONAL"]
    )
    bucket = api.get_bucket_by_name(os.environ["B2_BUCKET_NAME_PERSONAL"])
    for path in sorted(p for p in out.rglob("*") if p.is_file()):
        bucket.upload_local_file(
            local_file=str(path), file_name=f"oddstage/runs/{run}/{path.relative_to(out)}"
        )
    print(f"uploaded {out} to oddstage/runs/{run}/", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="a Hugging Face model id")
    ap.add_argument("--records", type=Path, required=True)
    ap.add_argument("--judge", action="store_true", help="the records are judge records")
    ap.add_argument("--evidence-first", action="store_true")
    ap.add_argument("--contexts", type=Path, help="eval contexts to answer after training")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--run", help="upload --out to B2 under this name")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--micro-batch", type=int, default=8)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--max-length", type=int)
    ap.add_argument("--track", action="store_true", help="log the run to MLflow")
    args = ap.parse_args()
    if args.batch % args.micro_batch:
        ap.error("--batch must be a multiple of --micro-batch")
    max_length = args.max_length or (6144 if args.judge else 2048)
    args.out.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    chat_kwargs = thinking_switch(tokenizer)
    examples = load_examples(args.records, args.judge, not args.evidence_first, chat_kwargs)
    lengths = check_lengths(tokenizer, examples, max_length)
    # The Hub rotary kernel needs q, k, cos and sin in one dtype, which autocast over
    # fp32 weights breaks.
    get_kernel_mapping_transformers().pop("rotary_pos_emb", None)
    model = AutoModelForCausalLM.from_pretrained(
        args.base, dtype=torch.float32, use_kernels=True, device_map="cuda"
    )
    check_kernels(model, tokenizer, examples[0])

    tracking = args.track
    if tracking and not os.environ.get("MLFLOW_TRACKING_URI"):
        ap.error("--track needs MLFLOW_TRACKING_URI")
    name = args.run or f"{args.base.split('/')[-1]}-{'judge' if args.judge else 'player'}"
    if tracking:
        mlflow.set_experiment("oddstage-training")
    with mlflow.start_run(run_name=name) if tracking else contextlib.nullcontext():
        train(args, model, tokenizer, examples, lengths, max_length, name, tracking)


def train(args, model, tokenizer, examples, lengths, max_length, name, tracking) -> None:
    """Trains, writes metrics.json and the model, answers the contexts, and uploads."""
    config = SFTConfig(
        output_dir=str(args.out / "trainer"),
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=0.03,
        per_device_train_batch_size=args.micro_batch,
        gradient_accumulation_steps=args.batch // args.micro_batch,
        gradient_checkpointing=False,
        train_sampling_strategy="group_by_length",
        bf16=True,
        max_length=max_length,
        packing=False,
        seed=args.seed,
        logging_steps=5,
        save_strategy="no",
        run_name=name,
        report_to="mlflow" if tracking else "none",
    )
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=Dataset.from_list(examples),
        processing_class=tokenizer,
    )
    torch.cuda.reset_peak_memory_stats()
    began = time.monotonic()
    trainer.train()
    seconds = time.monotonic() - began
    steps = trainer.state.global_step
    tokens = steps * args.batch * sum(lengths) / len(lengths)
    metrics = {
        "base": args.base,
        "kind": "judge" if args.judge else "player",
        "examples": len(examples),
        "steps": steps,
        "seconds": round(seconds, 1),
        "tokens_per_second": round(tokens / seconds),
        "peak_memory_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
        "mean_tokens": round(sum(lengths) / len(lengths)),
        "log": trainer.state.log_history,
    }
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=1))
    summary = {k: v for k, v in metrics.items() if k != "log"}
    print(json.dumps(summary), file=sys.stderr)
    if tracking:
        mlflow.log_metrics({k: summary[k] for k in RUN_METRICS})
        mlflow.set_tags({"base": args.base, "kind": summary["kind"], "records": args.records.name})
        if args.run:
            mlflow.set_tag("b2_path", f"oddstage/runs/{args.run}/")

    trainer.save_model(str(args.out / "model"))
    tokenizer.save_pretrained(str(args.out / "model"))
    if args.contexts:
        began = time.monotonic()
        answer_contexts(model, tokenizer, args.contexts, args.out)
        metrics["answer_seconds"] = round(time.monotonic() - began, 1)
        (args.out / "metrics.json").write_text(json.dumps(metrics, indent=1))
        print(json.dumps({"answer_seconds": metrics["answer_seconds"]}), file=sys.stderr)
    if args.run:
        upload(args.out, args.run)


if __name__ == "__main__":
    main()
