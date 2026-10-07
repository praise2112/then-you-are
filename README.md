# Then You Are

A word game you play against friends or an AI, and another AI judges every move. Play at
[thenyouare.com](https://thenyouare.com).

In *Then I Am*, the first game, each move has to beat the one before it: a rock, then
"I am a hammer", then "I am rust". A judge model rules on every move against the game's
written rubric, and one failed move ends the match.

The AI player is Qwen3.5-0.8B, fine-tuned on the games. On 394 positions from
game variants it never saw in training, 68.5% of its moves stand, up from 19.0% before
training. Qwen3.5-4B, prompted the same way without fine-tuning, gets 55.1%. It runs on
4 CPU cores with no GPU.

How the models were built, with every number and its source:
[How it was built](https://thenyouare.com/how-it-was-built). The model's weights, GGUF and card:
[Praise2112/then-you-are-house-0.8b](https://huggingface.co/Praise2112/then-you-are-house-0.8b).

## How a model is made

```mermaid
flowchart LR
    games[5 hand-written games] --> gen[Generator and 5 filters<br/>237 written, 35 kept]
    gen --> train[31 training games]
    gen --> held[9 held-back games]
    held --> test[Test: 394 frozen positions]
    train --> matches[Flash vs Luna<br/>3,737 judged matches]
    matches --> sft[SFT<br/>14,941 examples]
    sft --> pref[Preference optimization<br/>two rounds]
    pref --> rl[RL with GRPO<br/>judge as reward]
    rl --> gguf[Q4_K_M file, 517 MiB<br/>llama.cpp on Cloud Run]
    test -.->|scores every stage| sft & pref & rl
```

## How the code fits together

```mermaid
flowchart TB
    web[frontend<br/>React, Vite] --> server[arena_server<br/>FastAPI, SSE, Postgres]
    server --> core[arena_core<br/>rules and state, no I/O]
    server --> judge[arena_judge<br/>judge prompts, schemas, model calls]
    server --> house[deploy/house<br/>the AI player on llama.cpp]
    evals[arena_evals<br/>data generation, held-out test, golden set] --> core
    evals --> judge
    trainer[arena_train<br/>SFT, preference, GRPO scripts] --> evals
```

`arena_evals` plays matches through the same engine and judge code as the live game, and
never imports the server. Every paid model call it makes is recorded in a SQLite file, so
a stopped run resumes without paying twice, and every command takes a budget.

## Running it

To run the whole game in Docker:

```bash
cp .env.example .env                            # then fill in the keys you need
docker compose up --build                       # http://127.0.0.1:8000
```

For development:

```bash
uv sync
cp .env.example .env                            # then fill in the keys you need
docker compose up -d db                         # Postgres on 5433
docker exec oddstage-db-1 psql -U oddstage -c "create database oddstage_test"
set -a; source .env; set +a
uv run uvicorn arena_server.app:app --reload    # http://127.0.0.1:8000

cd frontend && npm install && npm run dev       # http://127.0.0.1:5173
```

The dev server proxies the API paths to the backend. `npm run gen` regenerates the
TypeScript types from the server's OpenAPI document.

## Reproducing training

Each step is one command. Every script prints its full usage with `--help`.

1. Play and judge matches: `python -m arena_evals.datagen.run start <run> --budget <usd>`
2. Build the training set: `python -m arena_evals.datagen.trainset <runs...> --out train`
3. SFT: `arena_train/sft.py --base Qwen/Qwen3.5-0.8B --records player.jsonl ...`
4. Mine preference pairs and train: `arena_evals.datagen.flashmine`, then
   `arena_train/prefs.py --method ipo ...`
5. RL: `python -m arena_train.grpo <run> --model <path> --budget <usd>`
6. Score any model on the held-out test: `python -m arena_evals.train_eval answer`, then
   `score <row> --budget <usd>`

The training scripts are single files with pinned dependencies, and they ran on rented
A100s through [dstack](https://dstack.ai). `deploy/house/house.sh` builds and deploys the
AI player's model service.

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
TEST_DATABASE_URL=postgresql://oddstage:oddstage@localhost:5433/oddstage_test uv run pytest -q
cd frontend && npm run build
```

CI runs the same checks on every push.
