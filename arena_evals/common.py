"""Shared setup for the offline tools: API key, model registry, retry with backoff."""

import asyncio
import os
import random
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import yaml

from arena_judge.caller import CallError, ModelCaller, ModelSpec

MODELS_PATH = Path(__file__).parents[1] / "arena_server" / "models.yaml"
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def load_model(ref: str) -> ModelSpec:
    registry = yaml.safe_load(MODELS_PATH.read_text())
    if ref not in registry:
        raise KeyError(f"no model named {ref} in models.yaml")
    return ModelSpec.model_validate(registry[ref])


def model_label(spec: ModelSpec) -> str:
    """The model id, with the pinned provider when there is one: deepseek/x@fireworks."""
    only = (spec.provider or {}).get("only") or []
    return f"{spec.model}@{only[0]}" if only else spec.model


def make_caller(judge_ref: str = "judge-v1", opponent_ref: str = "opponent-v1") -> ModelCaller:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    return ModelCaller(key, load_model(judge_ref), load_model(opponent_ref))


async def with_backoff[T](fn: Callable[[], Awaitable[T]], *, tries: int = 6) -> T:
    """Retry a call on rate limits, server errors and timeouts, honouring Retry-After."""
    for attempt in range(tries):
        try:
            return await fn()
        except (CallError, httpx.HTTPStatusError, httpx.TransportError) as e:
            status = getattr(e, "status", None) or getattr(
                getattr(e, "response", None), "status_code", None
            )
            if status is not None and status not in RETRY_STATUSES:
                raise
            if attempt == tries - 1:
                raise
            retry_after = getattr(getattr(e, "response", None), "headers", {}).get("Retry-After")
            wait = (
                float(retry_after) if retry_after else min(30.0, 0.5 * 2**attempt) + random.random()
            )
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")
