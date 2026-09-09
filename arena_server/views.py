"""Response models. Exported into OpenAPI so the browser client is generated from them."""

from typing import Literal

from pydantic import BaseModel

from arena_judge.schema import EndReason, HostPayload, Outcome, ScoringPayload


class RubricView(BaseModel):
    name: str
    description: str
    max_points: int


class DemoOpening(BaseModel):
    token: str
    emoji: str
    detail: str
    reveal: str


class DemoMoveView(BaseModel):
    actor: Literal["p1", "p2"]
    text: str
    emoji: str


class DemoPoints(BaseModel):
    name: str
    earned: int
    max_points: int


class LandingOpeningView(BaseModel):
    token: str
    emoji: str
    detail: str
    examples: list[str]


class DemoView(BaseModel):
    opening: DemoOpening
    moves: list[DemoMoveView]
    headline: str
    points: list[DemoPoints]
    openings: list[LandingOpeningView]


class TemplateView(BaseModel):
    slug: str
    title: str
    tagline: str
    premise: str
    mode: Literal["escalation", "showcase"]
    rounds: int | None
    rubric: list[RubricView]
    rules_text: str
    max_chars: int
    move_prefix: str
    move_example: str
    move_hint: str
    move_budget: int
    score_max: int
    host_name: str
    demo: DemoView


class TurnView(BaseModel):
    seq: int
    round_n: int
    actor: Literal["p1", "p2"]
    move_text: str
    outcome: Outcome
    scoring: ScoringPayload | None
    host: HostPayload | None
    points: int | None


class RoundView(BaseModel):
    """A dealt card. The truth is filled in only once both answers are judged."""

    round_n: int
    token: str
    emoji: str
    detail: str
    truth: str | None


class MatchSnapshot(BaseModel):
    id: str
    template_id: str
    title: str
    mode: Literal["escalation", "showcase"]
    status: Literal["active", "awaiting_judgment", "paused", "ended", "abandoned"]
    state_version: int
    seed_token: str
    seed_emoji: str
    rounds: list[RoundView]
    stage_name: str
    opponent_name: str
    to_move: Literal["p1", "p2"]
    winner: Literal["p1", "p2"] | None
    end_reason: EndReason | None
    points_p1: int
    points_p2: int
    judged_moves: int
    move_budget: int
    transcript: list[TurnView]
    created_at: str
    is_public: bool
    is_yours: bool = False


class SessionView(BaseModel):
    stage_name: str
    list_duels: bool


class StageView(BaseModel):
    """Public matches in play, and how many duels have finished."""

    live: list[MatchSnapshot]
    duels_played: int


class Replay(MatchSnapshot):
    share_text: str
    highlight_seq: int | None
    is_curated: bool
