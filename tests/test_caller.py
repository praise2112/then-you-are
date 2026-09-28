import asyncio
import json

import httpx

from arena_core.template import load_template
from arena_judge.caller import (
    CallError,
    CallResult,
    ModelCaller,
    ModelSpec,
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
