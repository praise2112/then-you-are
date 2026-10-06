import asyncio
import dataclasses
import json
import os
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from arena_core.template import Template, load_template
from arena_judge.caller import CallError, JudgeCall, ModelCaller
from arena_judge.schema import (
    BecauseClause,
    Evidence,
    Gates,
    HostPayload,
    JudgeResponse,
    ScoringPayload,
)
from arena_server.app import build_app
from arena_server.config import load_settings


@pytest.fixture(scope="session")
def template() -> Template:
    return load_template("then-i-am")


@pytest.fixture(scope="session")
def showcase() -> Template:
    return load_template("word-for-word")


def judge_response(
    verdict: str = "accept",
    confidence: str = "clear",
    gates: dict | None = None,
    scores: dict | None = None,
    truth_proximity: str = "none",
) -> JudgeResponse:
    base_gates = {
        "on_topic_and_coherent": True,
        "no_injection": True,
        "no_meta_move": True,
        "not_semantic_duplicate": True,
        "satisfies_criterion": verdict == "accept",
    }
    return JudgeResponse(
        scoring=ScoringPayload(
            gates=Gates(**{**base_gates, **(gates or {})}),
            evidence=Evidence(target_quote="a rock", mechanism="a hammer splits rock"),
            scores=scores or {"counter_strength": 3, "coherence": 3, "novelty": 2},
            confidence=confidence,  # type: ignore[arg-type]
            verdict=verdict,  # type: ignore[arg-type]
            truth_proximity=truth_proximity,  # type: ignore[arg-type]
        ),
        host=HostPayload(
            headline="The hammer speaks.",
            because_clause=BecauseClause(criterion="counter_strength", text="Rock splits."),
            quotable_line="Rock splits.",
            generated_emoji="🔨",
            coaching_line="A geologist would have won." if verdict == "fail" else None,
        ),
    )


class FakeCaller(ModelCaller):
    """Scripted judge and opponent. Each judge call pops the next response; None means an
    outage and an int means the provider refused with that HTTP status."""

    def __init__(
        self,
        rulings: list[JudgeResponse | None | int],
        opponent_moves: list[str],
        house_down: bool = False,
    ):
        self.rulings = list(rulings)
        self.house_down = house_down
        self.stand_in_moves = 0
        self.opponent_moves = list(opponent_moves)
        self.judged: list[str] = []
        self.hidden_seen: list[str] = []
        self.opponent_saw: list[list[str]] = []
        self.opponent_hidden: list[str] = []
        self.opponent_slots: list[int | None] = []
        self.wakes = 0

    async def aclose(self) -> None:
        return None

    async def wake_opponent(self) -> None:
        self.wakes += 1

    async def judge(self, template, transcript, previous, move, hidden="", spec=None) -> JudgeCall:
        self.judged.append(move)
        self.hidden_seen.append(hidden)
        response = self.rulings.pop(0) if self.rulings else judge_response()
        if isinstance(response, int):
            return JudgeCall(response=None, raw="", prompt_hash="test", error_status=response)
        if response and set(response.scoring.scores) != set(template.weights):
            response = response.model_copy(deep=True)
            response.scoring.scores = dict.fromkeys(template.weights, 3)
        raw = response.model_dump_json() if response else ""
        return JudgeCall(
            response=response, raw=raw, prompt_hash="test", latency_ms=1, attempts=["parsed"]
        )

    def opponent_stream(
        self, template, seat, card, transcript, hidden="", slot=None, spec=None
    ) -> AsyncIterator[str]:
        if spec is None and self.house_down:

            async def down() -> AsyncIterator[str]:
                raise CallError("the House is down", status=503)
                yield ""

            return down()
        self.stand_in_moves += spec is not None
        self.opponent_saw.append(list(transcript))
        self.opponent_slots.append(slot)
        self.opponent_hidden.append(hidden)
        move = (
            self.opponent_moves.pop(0) if self.opponent_moves else "I am a bucket, water-holding."
        )

        async def gen() -> AsyncIterator[str]:
            for word in move.split(" "):
                yield word + " "

        return gen()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def run_app(caller: ModelCaller):
    """Boots the app on the test database. Returns the app, its lifespan to close, and a client."""
    settings = dataclasses.replace(
        load_settings(),
        database_url=os.environ["TEST_DATABASE_URL"],
        curator_token="shh",
        session_secret="test-secret",
        oauth_clients={"github": ("id", "secret"), "discord": ("id", "secret")},
    )
    app = build_app(settings, caller)
    manager = LifespanManager(app)
    await manager.__aenter__()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return app, manager, client


async def settle(app) -> None:
    while app.state.service.tasks:
        await asyncio.gather(*app.state.service.tasks, return_exceptions=True)


def events_of(app, match_id: str) -> list[tuple[str, str, dict]]:
    stream = app.state.bus.streams[match_id]
    return [(e.id, e.name, json.loads(e.data)) for e in stream.events]
