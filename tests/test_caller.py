import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from arena_core.template import load_template
from arena_judge.caller import (
    CallError,
    CallResult,
    ModelCaller,
    ModelSpec,
    Prices,
    parse_judge,
    salvage_judge,
)
from tests.conftest import judge_response

NAMES = ["counter_strength", "coherence", "novelty"]


def test_parse_accepts_json_wrapped_in_prose():
    raw = "Here you go:\n" + judge_response().model_dump_json() + "\nDone."
    parsed = parse_judge(raw, NAMES)
    assert parsed is not None
    assert parsed.scoring.verdict == "accept"


def test_parse_fills_a_missing_host_block():
    scoring_only = json.loads(judge_response(verdict="fail").model_dump_json())["scoring"]
    parsed = parse_judge(json.dumps(scoring_only), NAMES)
    assert parsed is not None
    assert parsed.host.headline == "The move falls."


def test_parse_rejects_scores_that_do_not_match_the_rubric():
    data = json.loads(judge_response().model_dump_json())
    data["scoring"]["scores"] = {"counter_strength": 3, "economy": 4}
    assert parse_judge(json.dumps(data), NAMES) is None


def test_salvage_reads_gates_and_verdict_out_of_broken_json():
    raw = (
        '{"scoring": {"gates": {"on_topic_and_coherent": true, "no_injection": true, '
        '"no_meta_move": true, "not_semantic_duplicate": true, "satisfies_criterion": false}, '
        '"scores": {"counter_strength": 1, "coherence": 3, "novelty": 0}, '
        '"confidence": "clear", "verdict": "fail", "host": {"headline": "unterminated'
    )
    assert parse_judge(raw, NAMES) is None
    salvaged = salvage_judge(raw, NAMES)
    assert salvaged is not None
    assert salvaged.scoring.verdict == "fail"
    assert salvaged.scoring.scores == {"counter_strength": 1, "coherence": 3, "novelty": 0}


def test_salvage_gives_up_without_a_verdict():
    assert salvage_judge("the judge wandered off", NAMES) is None


def test_judge_calls_the_given_spec_and_keeps_the_reasoning():
    seen: list[str] = []

    class Spy(ModelCaller):
        async def complete(self, spec, messages, **extra):
            seen.append(spec.model)
            return CallResult(
                text=judge_response().model_dump_json(), latency_ms=1, reasoning="because"
            )

    flash = ModelSpec(model="flash", display_name="Flash")
    luna = ModelSpec(model="luna", display_name="Luna")
    caller = Spy("key", flash, flash)
    template = load_template("then-i-am")
    call = asyncio.run(caller.judge(template, [], "a rock", "I am a river.", spec=luna))
    assert seen == ["luna"]
    assert call.reasoning == "because"
    assert call.response is not None
    asyncio.run(caller.aclose())


def test_the_judge_leaves_client_errors_to_the_backoff_and_retries_a_server_error_once():
    calls: list[int] = []

    class Failing(ModelCaller):
        def __init__(self, status: int):
            spec = ModelSpec(model="j", display_name="J")
            super().__init__("key", spec, spec)
            self.status = status

        async def complete(self, spec, messages, **extra):
            calls.append(self.status)
            raise CallError("upstream", self.status, 7.0)

    template = load_template("then-i-am")
    for status, tries in ((402, 1), (429, 1), (503, 2)):
        calls.clear()
        call = asyncio.run(Failing(status).judge(template, [], "a rock", "I am a hammer."))
        assert call.response is None and len(calls) == tries
        assert call.error_status == status and call.error_retry_after == 7.0


def test_a_spec_with_a_base_url_goes_there_without_the_openrouter_key(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    remote = ModelSpec(model="flash", display_name="Flash")
    local = ModelSpec(
        model="student",
        display_name="Student",
        base_url="http://localhost:8080/v1/",
        chat_template_kwargs={"enable_thinking": False},
    )
    caller = ModelCaller("secret", remote, remote)
    for spec in (local, remote):
        assert asyncio.run(caller.complete(spec, [{"role": "user", "content": "hi"}])).text == "ok"
    asyncio.run(caller.aclose())

    to_local, to_remote = seen
    assert str(to_local.url) == "http://localhost:8080/v1/chat/completions"
    assert "authorization" not in to_local.headers
    assert json.loads(to_local.content)["chat_template_kwargs"] == {"enable_thinking": False}
    assert str(to_remote.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert to_remote.headers["authorization"] == "Bearer secret"
    assert "chat_template_kwargs" not in json.loads(to_remote.content)


def test_a_direct_spec_sends_its_own_key_and_records_the_priced_cost(monkeypatch):
    seen: list[httpx.Request] = []
    usage = {"prompt_cache_hit_tokens": 2_000_000, "prompt_cache_miss_tokens": 1_000_000}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        message = {"content": "ok", "reasoning_content": "hm"}
        body = {"choices": [{"message": message}], "usage": usage | {"completion_tokens": 10**6}}
        return httpx.Response(200, json=body)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setenv("DIRECT_KEY", "own")
    direct = ModelSpec(
        model="flash",
        display_name="Flash",
        reasoning_effort="low",
        base_url="https://api.example.com",
        api_key_env="DIRECT_KEY",
        prices=Prices(cache_hit=0.01, cache_miss=0.1, output=1.0),
    )
    caller = ModelCaller("secret", direct, direct)
    result = asyncio.run(caller.complete(direct, [{"role": "user", "content": "hi"}]))
    asyncio.run(caller.aclose())

    assert seen[0].headers["authorization"] == "Bearer own"
    body = json.loads(seen[0].content)
    assert body["reasoning_effort"] == "low" and "reasoning" not in body and "usage" not in body
    assert result.reasoning == "hm"
    assert result.cost_usd == pytest.approx(2 * 0.01 + 0.1 + 1.0)


def test_a_direct_spec_with_thinking_off_asks_the_endpoint_not_to_think(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setenv("DIRECT_KEY", "own")
    fast = ModelSpec(
        model="flash",
        display_name="Flash",
        thinking=False,
        base_url="https://api.example.com",
        api_key_env="DIRECT_KEY",
    )
    caller = ModelCaller("secret", fast, fast)
    asyncio.run(caller.complete(fast, [{"role": "user", "content": "hi"}]))
    asyncio.run(caller.aclose())

    body = json.loads(seen[0].content)
    assert body["thinking"] == {"type": "disabled"} and "reasoning_effort" not in body


def test_weekday_peak_hours_cost_twice_as_much():
    prices = Prices(cache_hit=0, cache_miss=1.0, output=0, peak_hours_utc=[6])
    usage = {"prompt_cache_miss_tokens": 10**6}
    monday_peak = datetime(2026, 9, 28, 6, 30, tzinfo=UTC)
    assert prices.cost(usage, monday_peak) == 2.0
    assert prices.cost(usage, monday_peak.replace(hour=11)) == 1.0
    assert prices.cost(usage, datetime(2026, 9, 27, 6, 30, tzinfo=UTC)) == 1.0
