from collections.abc import AsyncIterator

import pytest

from arena_core.template import Template, load_template
from arena_judge.caller import JudgeCall, ModelCaller
from arena_judge.schema import (
    BecauseClause,
    Evidence,
    Gates,
    HostPayload,
    JudgeResponse,
    ScoringPayload,
)


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

    def __init__(self, rulings: list[JudgeResponse | None | int], opponent_moves: list[str]):
        self.rulings = list(rulings)
        self.opponent_moves = list(opponent_moves)
        self.judged: list[str] = []
        self.hidden_seen: list[str] = []
        self.opponent_saw: list[list[str]] = []
        self.opponent_hidden: list[str] = []
        self.opponent_slots: list[int | None] = []

    async def aclose(self) -> None:
        return None

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
        self, template, seat, card, transcript, hidden="", slot=None
    ) -> AsyncIterator[str]:
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
