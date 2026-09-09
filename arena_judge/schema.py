"""Judge, host, and SSE wire schemas. Shared by the live server and the offline harness."""

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["clear", "lean", "coin_flip"]
Verdict = Literal["accept", "fail"]
TruthProximity = Literal["hit", "near", "none"]
EndReason = Literal["sudden_death", "move_cap_points", "rounds_complete", "resign", "abandoned"]

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
    truth_proximity: TruthProximity = "none"


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


class JudgeResponse(BaseModel):
    """The one-call judge output: scoring block first, host block behind it."""

    scoring: ScoringPayload
    host: HostPayload


def route_outcome(payload: ScoringPayload) -> Outcome:
    if not payload.gates.hygiene_passed():
        return "semantic_reject"
    if payload.confidence == "coin_flip":
        return "semantic_uncertain"
    return payload.verdict


class TurnRejected(BaseModel):
    seq: int | None = None
    outcome: Literal["deterministic_invalid", "semantic_reject"]
    reason_text: str
    strikes: int
    nudge_text: str | None = None


class JudgeStarted(BaseModel):
    seq: int


class Ruling(BaseModel):
    seq: int
    round_n: int
    actor: Literal["p1", "p2"]
    move_text: str
    outcome: Outcome
    scoring: ScoringPayload
    host: HostPayload
    badges: list[str]
    points: int
    points_p1: int
    points_p2: int
    to_move: Literal["p1", "p2"]
    state_version: int


class MoveToken(BaseModel):
    seq: int
    text: str


class JudgePaused(BaseModel):
    seq: int
    host_text: str


class JudgeResumed(BaseModel):
    seq: int


class RoundRevealed(BaseModel):
    """Showcase only: both answers are in, so the card's truth may be shown."""

    round_n: int
    token: str
    emoji: str
    detail: str
    truth: str
    state_version: int


class MatchEnded(BaseModel):
    end_reason: EndReason
    winner: Literal["p1", "p2"] | None
    points_p1: int
    points_p2: int
    highlight_seq: int | None
    coaching_line: str | None = None
    share_text: str
    replay_id: str
    state_version: int


class StateResync(BaseModel):
    state_version: int
