"""Response models. Exported into OpenAPI so the browser client is generated from them."""

from typing import Literal

from pydantic import BaseModel

from arena_judge.schema import HostPayload, Outcome, ScoringPayload


class RubricView(BaseModel):
    name: str
    description: str


class TemplateView(BaseModel):
    slug: str
    title: str
    premise: str
    rubric: list[RubricView]
    rules_text: str
    max_chars: int
    move_budget: int
    host_name: str


class TurnView(BaseModel):
    seq: int
    actor: Literal["p1", "p2"]
    move_text: str
    outcome: Outcome
    scoring: ScoringPayload | None
    host: HostPayload | None


class MatchSnapshot(BaseModel):
    id: str
    template_id: str
    status: Literal["active", "awaiting_judgment", "paused", "ended", "abandoned"]
    state_version: int
    seed_token: str
    seed_emoji: str
    stage_name: str
    opponent_name: str
    to_move: Literal["p1", "p2"]
    winner: Literal["p1", "p2"] | None
    end_reason: Literal["sudden_death", "move_cap_points", "resign", "abandoned"] | None
    points_p1: int
    points_p2: int
    judged_moves: int
    move_budget: int
    transcript: list[TurnView]
    created_at: str


class Replay(MatchSnapshot):
    share_text: str
    highlight_seq: int | None
