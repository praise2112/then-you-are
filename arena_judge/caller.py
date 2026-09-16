"""One OpenRouter caller for judge and opponent, with the judge parse ladder."""

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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


class ModelSpec(BaseModel):
    model: str
    display_name: str
    temperature: float = 1.0
    reasoning_effort: str | None = None
    provider: dict[str, Any] | None = None


class CallError(Exception):
    """The upstream call failed: network, timeout, or a non-2xx status (kept in `status`)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


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

    async def aclose(self) -> None:
        await self.client.aclose()

    def _body(self, spec: ModelSpec, messages: list[dict], **extra: Any) -> dict:
        body: dict[str, Any] = {
            "model": spec.model,
            "messages": messages,
            "temperature": spec.temperature,
            "usage": {"include": True},
            **extra,
        }
        if spec.reasoning_effort:
            body["reasoning"] = {"effort": spec.reasoning_effort}
        if spec.provider:
            body["provider"] = spec.provider
        return body

    async def complete(self, spec: ModelSpec, messages: list[dict], **extra: Any) -> CallResult:
        t0 = time.monotonic()
        try:
            resp = await self.client.post(OPENROUTER_URL, json=self._body(spec, messages, **extra))
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise CallError(str(e), e.response.status_code) from e
        except (httpx.HTTPError, ValueError) as e:
            raise CallError(str(e)) from e
        if "choices" not in data:
            raise CallError(f"no choices in response: {json.dumps(data)[:300]}")
        usage = data.get("usage") or {}
        message = data["choices"][0]["message"]
        return CallResult(
            text=message.get("content") or "",
            latency_ms=int((time.monotonic() - t0) * 1000),
            reasoning=message.get("reasoning") or None,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            cost_usd=float(usage.get("cost", 0.0)),
        )

    async def stream(self, spec: ModelSpec, messages: list[dict]) -> AsyncIterator[str]:
        body = self._body(spec, messages, stream=True)
        try:
            async with self.client.stream("POST", OPENROUTER_URL, json=body) as resp:
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
        self, template: Template, card: str, transcript: list[str], hidden: str = ""
    ) -> AsyncIterator[str]:
        messages = render_opponent_messages(template, card, transcript, hidden)
        return self.stream(self.opponent_spec, messages)


def parse_judge(raw: str, rubric_names: list[str]) -> JudgeResponse | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
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
