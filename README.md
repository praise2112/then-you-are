# Oddstage

A judged creative-game arena. Humans, small fine-tuned models, and frontier models
play word games that a model referee scores against a written rubric.

First game: **Then I Am**, an escalation duel. You name a form. Your opponent names
a form that beats it. The Judge decides whether the counter holds.

## Stack

Python 3.12, FastAPI, Pydantic v2, PostgreSQL on the backend. Vite, React, and
TypeScript on the frontend.

Packages: `arena_core` is pure game logic with no I/O. `arena_judge` holds the
scoring and host schemas, and is imported by both the live server and the offline
eval harness. `arena_server` is the HTTP and SSE layer.

## Running it

```bash
uv sync
docker compose up -d db
uv run uvicorn arena_server.app:app --reload    # http://127.0.0.1:8000

cd frontend && npm install && npm run dev       # http://127.0.0.1:5173
```

The dev server proxies `/api` to the backend.

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest -q
cd frontend && npm run build
```
