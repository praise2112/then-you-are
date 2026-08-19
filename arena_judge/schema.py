"""Judge wire schemas. Shared by the live server and the offline eval harness."""

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["clear", "lean", "coin_flip"]
Verdict = Literal["accept", "fail"]

Outcome = Literal[
    "accept",
    "fail",
    "semantic_reject",
    "semantic_uncertain",
    "deterministic_invalid",
    "judge_unavailable",
]


class Gates(BaseModel):
    """Pass/fail checks on whether the move legitimately played the game."""

    on_topic_and_coherent: bool
    no_injection: bool
    no_meta_move: bool
    not_semantic_duplicate: bool
    satisfies_criterion: bool

    def hygiene_passed(self) -> bool:
        return (
            self.on_topic_and_coherent
            and self.no_injection
            and self.no_meta_move
            and self.not_semantic_duplicate
        )


class Evidence(BaseModel):
    target_quote: str
    mechanism: str


class ScoringPayload(BaseModel):
    """Layer 2. Score keys must equal the template's rubric names."""

    gates: Gates
    evidence: Evidence
    scores: dict[str, int]
    confidence: Confidence
    verdict: Verdict


class BecauseClause(BaseModel):
    criterion: str
    text: str


class HostPayload(BaseModel):
    """Layer 3. Badges are injected by code, never authored by the model."""

    headline: str = Field(max_length=140)
    because_clause: BecauseClause
    quotable_line: str
    generated_emoji: str
    coaching_line: str | None = None
    badges: list[str] = Field(default_factory=list)


def route_outcome(payload: ScoringPayload) -> Outcome:
    if not payload.gates.hygiene_passed():
        return "semantic_reject"
    if payload.confidence == "coin_flip":
        return "semantic_uncertain"
    return payload.verdict
