"""One caller for judge and opponent, OpenRouter or a spec's own endpoint, with the judge parse
ladder."""

import json
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from arena_core.template import Template
from arena_judge.prompt import judge_prompt_hash, render_judge_prompt, render_opponent_messages
from arena_judge.schema import (
    BecauseClause,
    Evidence,
    Gates,
    HostPayload,
    JudgeResponse,
    ScoringPayload,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LOCAL_TIMEOUT_S = 300.0


class Prices(BaseModel):
    """Dollars per million tokens; the weekday peak hours (UTC) cost twice as much."""

    cache_hit: float
    cache_miss: float
    output: float
    peak_hours_utc: list[int] = []

    def cost(self, usage: dict, now: datetime) -> float:
        rate = 2 if now.weekday() < 5 and now.hour in self.peak_hours_utc else 1
        tokens = (
            usage.get("prompt_cache_hit_tokens", 0) * self.cache_hit
            + usage.get("prompt_cache_miss_tokens", 0) * self.cache_miss
            + usage.get("completion_tokens", 0) * self.output
        )
        return rate * tokens / 1e6


class ModelSpec(BaseModel):
    model: str
    display_name: str
    temperature: float = 1.0
    reasoning_effort: str | None = None
    thinking: bool | None = None
    slots: int | None = None
    provider: dict[str, Any] | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    prices: Prices | None = None
    chat_template_kwargs: dict[str, Any] | None = None


class CallError(Exception):
    """The upstream call failed: network, timeout, or a non-2xx status (kept in `status`,
    with the provider's Retry-After seconds when it sent one)."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass
class CallResult:
    text: str
    latency_ms: int
    reasoning: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass
class JudgeCall:
    response: JudgeResponse | None
    raw: str
    prompt_hash: str
    reasoning: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    attempts: list[str] = field(default_factory=list)
    error_status: int | None = None
    error_retry_after: float | None = None


class ModelCaller:
    def __init__(
        self, api_key: str, judge: ModelSpec, opponent: ModelSpec, timeout_s: float = 30.0
    ):
        self.judge_spec = judge
        self.opponent_spec = opponent
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "Oddstage"},
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )
        # A spec with its own base_url is sent without the OpenRouter key, and a CPU server
        # can take a minute on a cold prompt.
        self.local = httpx.AsyncClient(timeout=httpx.Timeout(LOCAL_TIMEOUT_S, connect=10.0))

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.local.aclose()

    def _route(self, spec: ModelSpec) -> tuple[httpx.AsyncClient, str, dict[str, str]]:
        """The client, URL and extra headers; a base_url spec carries only its own key."""
        if not spec.base_url:
            return self.client, OPENROUTER_URL, {}
        headers = {}
        if spec.api_key_env:
            key = os.environ.get(spec.api_key_env)
            if not key:
                raise ValueError(f"{spec.api_key_env} is not set")
            headers["Authorization"] = f"Bearer {key}"
        return self.local, f"{spec.base_url.rstrip('/')}/chat/completions", headers

    def _body(self, spec: ModelSpec, messages: list[dict], **extra: Any) -> dict:
        body: dict[str, Any] = {
            "model": spec.model,
            "messages": messages,
            "temperature": spec.temperature,
            **extra,
        }
        if spec.base_url:
            if spec.reasoning_effort:
                body["reasoning_effort"] = spec.reasoning_effort
            if spec.thinking is not None:
                body["thinking"] = {"type": "enabled" if spec.thinking else "disabled"}
        else:
            body["usage"] = {"include": True}
            if spec.reasoning_effort:
                body["reasoning"] = {"effort": spec.reasoning_effort}
        if spec.provider:
            body["provider"] = spec.provider
        if spec.chat_template_kwargs:
            body["chat_template_kwargs"] = spec.chat_template_kwargs
        return body

    async def complete(self, spec: ModelSpec, messages: list[dict], **extra: Any) -> CallResult:
        t0 = time.monotonic()
        client, url, headers = self._route(spec)
        try:
            resp = await client.post(url, json=self._body(spec, messages, **extra), headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            wait = e.response.headers.get("Retry-After")
            retry_after = float(wait) if wait and wait.replace(".", "", 1).isdigit() else None
            raise CallError(str(e), e.response.status_code, retry_after) from e
        except (httpx.HTTPError, ValueError) as e:
            raise CallError(str(e)) from e
        if "choices" not in data:
            raise CallError(f"no choices in response: {json.dumps(data)[:300]}")
        usage = data.get("usage") or {}
        message = data["choices"][0]["message"]
        cost = usage.get("cost")
        if cost is None and spec.prices:
            cost = spec.prices.cost(usage, datetime.now(UTC))
        return CallResult(
            text=message.get("content") or "",
            latency_ms=int((time.monotonic() - t0) * 1000),
            reasoning=message.get("reasoning") or message.get("reasoning_content") or None,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            cost_usd=float(cost or 0.0),
        )

    async def stream(
        self, spec: ModelSpec, messages: list[dict], **extra: Any
    ) -> AsyncIterator[str]:
        body = self._body(spec, messages, stream=True, **extra)
        client, url, headers = self._route(spec)
        try:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    chunk = json.loads(line[6:])
                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
        except (httpx.HTTPError, ValueError) as e:
            raise CallError(str(e)) from e

    async def judge(
        self,
        template: Template,
        transcript: list[str],
        previous: str,
        move: str,
        hidden: str = "",
        spec: ModelSpec | None = None,
    ) -> JudgeCall:
        """Hard timeout, one structured retry, substring salvage, then no response."""
        prompt = render_judge_prompt(template, transcript, previous, move, hidden)
        call = JudgeCall(response=None, raw="", prompt_hash=judge_prompt_hash(template))
        messages: list[dict] = [{"role": "user", "content": prompt}]
        for attempt in range(2):
            try:
                result = await self.complete(
                    spec or self.judge_spec, messages, response_format={"type": "json_object"}
                )
            except CallError as e:
                call.attempts.append(f"call_error: {e}")
                call.error_status = e.status
                call.error_retry_after = e.retry_after
                # A client error (credit, rate limit, bad request) is not retried here.
                if e.status is not None and 400 <= e.status < 500:
                    break
                continue
            call.raw = result.text
            call.reasoning = result.reasoning
            call.latency_ms += result.latency_ms
            call.tokens_in += result.tokens_in
            call.tokens_out += result.tokens_out
            call.cost_usd += result.cost_usd
            parsed = parse_judge(result.text, list(template.weights))
            if parsed:
                call.response = parsed
                call.attempts.append("parsed" if attempt == 0 else "parsed_on_retry")
                return call
            call.attempts.append("unparseable")
            messages = [
                *messages,
                {"role": "assistant", "content": result.text},
                {
                    "role": "user",
                    "content": "That was not the JSON object requested. "
                    "Reply with only the JSON object.",
                },
            ]
        salvaged = salvage_judge(call.raw, list(template.weights))
        if salvaged:
            call.response = salvaged
            call.attempts.append("salvaged")
        return call

    def opponent_stream(
        self,
        template: Template,
        seat: str,
        card: str,
        transcript: list[str],
        hidden: str = "",
        slot: int | None = None,
        spec: ModelSpec | None = None,
    ) -> AsyncIterator[str]:
        """The House's move as it streams, from `spec` instead when given; slot pins the
        conversation to one llama-server slot, so each call reuses the seat's cache there."""
        messages = render_opponent_messages(template, card, transcript, hidden, seat=seat)
        extra = {} if slot is None else {"id_slot": slot}
        return self.stream(spec or self.opponent_spec, messages, **extra)

    async def wake_opponent(self) -> None:
        """Asks a self-hosted House for /health, so a scaled-to-zero server starts booting
        before the House's first move; does nothing for a hosted opponent."""
        if self.opponent_spec.base_url:
            await self.local.get(
                self.opponent_spec.base_url.rstrip("/").removesuffix("/v1") + "/health"
            )


def extract_json(raw: str) -> dict | None:
    """The first JSON object inside a model reply, or None when there is none."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_judge(raw: str, rubric_names: list[str]) -> JudgeResponse | None:
    data = extract_json(raw)
    if data is None:
        return None
    if "scoring" not in data and "gates" in data:
        # A judge that skipped the host block still produced a usable scoring block.
        data = {"scoring": data, "host": None}
    if data.get("host") is None:
        data["host"] = _fallback_host(data.get("scoring") or {}, rubric_names[0])
    try:
        response = JudgeResponse.model_validate(data)
    except ValidationError:
        return None
    if set(response.scoring.scores) != set(rubric_names):
        return None
    return response


def _fallback_line(verdict: str) -> str:
    return "The move stands." if verdict == "accept" else "The move falls."


def _fallback_host(scoring: dict, criterion: str) -> dict:
    verdict = scoring.get("verdict", "accept")
    return {
        "headline": _fallback_line(verdict),
        "because_clause": {
            "criterion": criterion,
            "text": (scoring.get("evidence") or {}).get("mechanism", "The Judge gave no reason."),
        },
        "quotable_line": _fallback_line(verdict),
        "generated_emoji": "🎭",
        "coaching_line": None,
    }


def salvage_judge(raw: str, rubric_names: list[str]) -> JudgeResponse | None:
    """Pull gates, verdict and confidence out of broken JSON by pattern. Scores default to 0."""

    def flag(name: str) -> bool | None:
        m = re.search(rf'"{name}"\s*:\s*(true|false)', raw)
        return None if not m else m.group(1) == "true"

    gate_values = {g: flag(g) for g in Gates.model_fields}
    verdict = re.search(r'"verdict"\s*:\s*"(accept|fail)"', raw)
    confidence = re.search(r'"confidence"\s*:\s*"(clear|lean|coin_flip)"', raw)
    if verdict is None or confidence is None or any(v is None for v in gate_values.values()):
        return None
    scores = {}
    for name in rubric_names:
        m = re.search(rf'"{name}"\s*:\s*([0-4])', raw)
        scores[name] = int(m.group(1)) if m else 0
    scoring = ScoringPayload(
        gates=Gates(**{k: bool(v) for k, v in gate_values.items()}),
        evidence=Evidence(target_quote="", mechanism="salvaged from a broken judge reply"),
        scores=scores,
        confidence=confidence.group(1),  # type: ignore[arg-type]
        verdict=verdict.group(1),  # type: ignore[arg-type]
    )
    host = HostPayload(
        headline=_fallback_line(scoring.verdict),
        because_clause=BecauseClause(criterion=rubric_names[0], text="The Judge gave no reason."),
        quotable_line=_fallback_line(scoring.verdict),
        generated_emoji="🎭",
    )
    return JudgeResponse(scoring=scoring, host=host)
