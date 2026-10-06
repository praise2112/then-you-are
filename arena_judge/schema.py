"""Judge, host, and SSE wire schemas. Shared by the live server and the offline harness."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from arena_core.template import SCORE_MAX

Confidence = Literal["clear", "lean", "coin_flip"]
Verdict = Literal["accept", "fail"]
TruthProximity = Literal["hit", "near", "none"]
EndReason = Literal[
    "sudden_death",
    "move_cap_points",
    "rounds_complete",
    "resign",
    "abandoned",
    "forfeit",
    "unfilled",
]

Outcome = Literal[
    "accept",
    "fail",
    "semantic_reject",
    "semantic_uncertain",
    "deterministic_invalid",
    "forfeit",
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
    scores: dict[str, Annotated[int, Field(ge=0, le=SCORE_MAX)]]
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
    # The seat whose move came back; other clients ignore it.
    seat: str
    outcome: Literal["deterministic_invalid", "semantic_reject"]
    reason_text: str
    strikes: int
    nudge_text: str | None = None


class JudgeStarted(BaseModel):
    seq: int


class Ruling(BaseModel):
    seq: int
    round_n: int
    actor: str
    move_text: str
    outcome: Outcome
    scoring: ScoringPayload
    host: HostPayload
    points: int
    # Every seat's total once this ruling lands.
    totals: dict[str, int]
    to_move: str
    # The round the match is in once this ruling lands; past the budget when it ended.
    round_in_play: int
    state_version: int


class MoveToken(BaseModel):
    seq: int
    text: str


class JudgePaused(BaseModel):
    seq: int
    host_text: str
    move_text: str


class JudgeResumed(BaseModel):
    seq: int


class GuessOption(BaseModel):
    """One entry on the table during a call. The key says nothing about which is real."""

    key: str
    text: str


class GuessView(BaseModel):
    """A call made: who picked, what they picked (the truth or a player's bluff), who got paid."""

    actor: str
    picked: str
    points: int
    awarded_to: str


class GuessOpened(BaseModel):
    """Showcase only: every answer is judged and the guessers may call the real entry. Each
    guesser reads its own options from the snapshot."""

    round_n: int
    state_version: int


class RoundRevealed(BaseModel):
    """Showcase only: every call is in, so the card's truth may be shown."""

    round_n: int
    token: str
    emoji: str
    detail: str
    truth: str
    guesses: list[GuessView]
    totals: dict[str, int]
    state_version: int


class MatchEnded(BaseModel):
    end_reason: EndReason
    winner: str | None
    totals: dict[str, int]
    highlight_seq: int | None
    coaching_line: str | None = None
    share_text: str
    replay_id: str
    state_version: int


class SeatJoined(BaseModel):
    """A player took a seat at a table that is still filling."""

    seat: str
    state_version: int


class MatchStarted(BaseModel):
    """Every seat is filled and play begins."""

    state_version: int


class SeatSubmitted(BaseModel):
    """Showcase: a seat has written or called for the round in play. Says nothing about what."""

    seat: str
    state_version: int


class TurnChanged(BaseModel):
    """The turn passed without a ruling to show: a forfeit, a resign, or play moved on."""

    to_move: str
    turn_deadline: str | None
    round_in_play: int
    state_version: int
