"""Shared setup for the offline tools: API key, model registry, retry with backoff."""

import asyncio
import itertools
import os
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

import httpx

from arena_core.template import Template
from arena_judge.caller import CallError, JudgeCall, ModelCaller, ModelSpec
from arena_server.config import load_model as load_model

RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
WINDOW_OPEN = time(16, 30)
WINDOW_CLOSE = time(0, 30)
EMBED_URL = "https://openrouter.ai/api/v1/embeddings"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
EMBED_MODEL = "openai/text-embedding-3-small"
EMBED_BATCH = 256


def in_window(now: datetime) -> bool:
    return now.time() >= WINDOW_OPEN or now.time() < WINDOW_CLOSE


def seconds_until_open(now: datetime) -> float:
    opens = now.replace(hour=WINDOW_OPEN.hour, minute=WINDOW_OPEN.minute, second=0, microsecond=0)
    if opens <= now:
        opens += timedelta(days=1)
    return (opens - now).total_seconds()


def require_window(now_flag: bool) -> None:
    """Exits unless inside the off-peak window or the caller passed --now."""
    if not now_flag and not in_window(datetime.now(UTC)):
        raise SystemExit(
            f"outside the off-peak window ({WINDOW_OPEN:%H:%M} to {WINDOW_CLOSE:%H:%M} UTC); "
            "pass --now"
        )


async def credit_left(caller: ModelCaller) -> float:
    """Dollars left on the OpenRouter account; raises SystemExit when it cannot be read."""
    try:
        resp = await caller.client.get(CREDITS_URL)
        resp.raise_for_status()
        data = resp.json()["data"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise SystemExit(f"could not read the OpenRouter balance: {e}") from e
    return float(data["total_credits"]) - float(data["total_usage"])


def model_label(spec: ModelSpec) -> str:
    """The model id, with the pinned provider when there is one: deepseek/x@fireworks."""
    only = (spec.provider or {}).get("only") or []
    return f"{spec.model}@{only[0]}" if only else spec.model


def make_caller(judge_ref: str = "judge-v1", opponent_ref: str = "opponent-v1") -> ModelCaller:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    return ModelCaller(key, load_model(judge_ref), load_model(opponent_ref))


async def with_backoff[T](fn: Callable[[], Awaitable[T]], *, tries: int = 10) -> T:
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
            retry_after = getattr(e, "retry_after", None) or getattr(
                getattr(e, "response", None), "headers", {}
            ).get("Retry-After")
            wait = (
                float(retry_after) if retry_after else min(60.0, 0.5 * 2**attempt) + random.random()
            )
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")


async def embed_texts(
    caller: ModelCaller, texts: list[str], sem: asyncio.Semaphore
) -> list[list[float]]:
    """One vector per text, in order, fetched in batches under the semaphore."""

    async def post(chunk: list[str]) -> httpx.Response:
        resp = await caller.client.post(EMBED_URL, json={"model": EMBED_MODEL, "input": chunk})
        resp.raise_for_status()
        return resp

    async def one(chunk: list[str]) -> list[list[float]]:
        async with sem:
            resp = await with_backoff(lambda: post(chunk))
        return [row["embedding"] for row in resp.json()["data"]]

    chunks = [texts[i : i + EMBED_BATCH] for i in range(0, len(texts), EMBED_BATCH)]
    rows = await asyncio.gather(*(one(c) for c in chunks))
    return list(itertools.chain.from_iterable(rows))


async def judge_with_backoff(
    caller: ModelCaller,
    template: Template,
    transcript: list[str],
    previous: str,
    move: str,
    hidden: str,
    spec: ModelSpec | None = None,
) -> JudgeCall:
    """caller.judge under with_backoff. A call that failed on transport or a retryable status
    is retried; an unparseable reply is not. The returned call carries every attempt's cost."""
    tries: list[JudgeCall] = []

    async def once() -> JudgeCall:
        call = await caller.judge(template, transcript, previous, move, hidden, spec=spec)
        tries.append(call)
        failed_call = call.attempts and call.attempts[-1].startswith("call_error")
        if call.response is None and failed_call:
            raise CallError(
                f"judge call failed ({call.error_status})",
                call.error_status,
                call.error_retry_after,
            )
        return call

    try:
        result = await with_backoff(once)
    except CallError:
        result = tries[-1]
    for earlier in tries[:-1]:
        result.cost_usd += earlier.cost_usd
        result.tokens_in += earlier.tokens_in
        result.tokens_out += earlier.tokens_out
        result.latency_ms += earlier.latency_ms
        result.attempts = [*earlier.attempts, *result.attempts]
    return result
