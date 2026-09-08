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
cp .env.example .env                            # then fill in OPENROUTER_API_KEY
docker compose up -d db                         # Postgres on 5433
docker exec oddstage-db-1 psql -U oddstage -c "create database oddstage_test"
set -a; source .env; set +a
uv run uvicorn arena_server.app:app --reload    # http://127.0.0.1:8000

cd frontend && npm install && npm run dev       # http://127.0.0.1:5173
```

The dev server proxies `/api` to the backend.

## Mockups

Static HTML mockups of every Phase-1 screen live in `mockups/`. They share the
frontend's token sheet, so a palette change shows up in both. Serve them and open
the contact sheet:

```bash
python3 -m http.server 8090      # then open http://localhost:8090/mockups/
```

The methodology page is generated, not hand-written. Edit `mockups/methodology.md`
and run `python3 mockups/build_methodology.py`.

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
TEST_DATABASE_URL=postgresql://oddstage:oddstage@localhost:5433/oddstage_test uv run pytest -q
cd frontend && npm run build
```
