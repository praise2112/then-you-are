"""Pure match state and transitions. No framework imports, no network."""

import re
from dataclasses import dataclass, field
from typing import Literal

from arena_core.template import Template
from arena_judge.schema import Outcome

MatchStatus = Literal["active", "awaiting_judgment", "paused", "ended", "abandoned"]
EndReason = Literal["sudden_death", "move_cap_points", "resign", "abandoned"]
Actor = Literal["p1", "p2"]
Layer1Reason = Literal["empty", "too_long", "duplicate"]

JUDGED: tuple[Outcome, ...] = ("accept", "fail", "semantic_uncertain")
STANDING: tuple[Outcome, ...] = ("accept", "semantic_uncertain")
REFUSED: tuple[Outcome, ...] = ("deterministic_invalid", "semantic_reject")


class StaleVersionError(Exception):
    """The command carried an expected_version that no longer matches the match."""


@dataclass(frozen=True)
class Turn:
    seq: int
    actor: Actor
    move_text: str
    outcome: Outcome


@dataclass
class Match:
    id: str
    template_id: str
    template_version: int
    seed: str
    seed_emoji: str = ""
    status: MatchStatus = "active"
    state_version: int = 0
    to_move: Actor = "p1"
    turns: list[Turn] = field(default_factory=list)
    strikes: dict[Actor, int] = field(default_factory=lambda: {"p1": 0, "p2": 0})
    points: dict[Actor, int] = field(default_factory=lambda: {"p1": 0, "p2": 0})
    winner: Actor | None = None
    end_reason: EndReason | None = None

    @property
    def judged_moves(self) -> int:
        return sum(1 for t in self.turns if t.outcome in JUDGED)

    @property
    def standing_turn(self) -> Turn | None:
        for turn in reversed(self.turns):
            if turn.outcome in STANDING:
                return turn
        return None

    @property
    def standing_form(self) -> str:
        turn = self.standing_turn
        return turn.move_text if turn else self.seed

    @property
    def history(self) -> list[str]:
        return [t.move_text for t in self.turns if t.outcome in STANDING]


def other(actor: Actor) -> Actor:
    return "p2" if actor == "p1" else "p1"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def layer1(template: Template, move_text: str, match: Match) -> Layer1Reason | None:
    """Deterministic checks before the judge: empty, over the cap, exact duplicate."""
    if not move_text.strip():
        return "empty"
    if len(move_text) > template.move_constraints.max_chars:
        return "too_long"
    if normalize(move_text) in {normalize(h) for h in [match.seed, *match.history]}:
        return "duplicate"
    return None


def weighted_total(scores: dict[str, int], weights: dict[str, int]) -> int:
    return sum(weights.get(name, 0) * value for name, value in scores.items())


def _check_command(match: Match, actor: Actor, expected_version: int) -> None:
    if expected_version != match.state_version:
        raise StaleVersionError(f"expected {expected_version}, match is at {match.state_version}")
    if match.status in ("ended", "abandoned"):
        raise ValueError("match already ended")
    if actor != match.to_move:
        raise ValueError(f"{actor} played out of turn")


def apply_ruling(
    match: Match,
    actor: Actor,
    move_text: str,
    outcome: Outcome,
    expected_version: int,
    move_budget: int,
    points: int = 0,
) -> Match:
    """Record a judged or refused move and advance the match. One fail ends it."""
    _check_command(match, actor, expected_version)
    match.state_version += 1
    match.status = "active"

    if outcome in REFUSED:
        # Refusals hand the turn back. What a strike count means is the caller's business.
        match.strikes[actor] += 1
        return match

    match.turns.append(
        Turn(seq=len(match.turns) + 1, actor=actor, move_text=move_text, outcome=outcome)
    )
    match.points[actor] += points

    if outcome == "fail":
        match.status = "ended"
        match.winner = other(actor)
        match.end_reason = "sudden_death"
        return match

    match.to_move = other(actor)
    if match.judged_moves >= move_budget:
        match.status = "ended"
        match.end_reason = "move_cap_points"
        p1, p2 = match.points["p1"], match.points["p2"]
        # A tie goes to whoever holds the standing form, which is the actor who just moved.
        match.winner = "p1" if p1 > p2 else "p2" if p2 > p1 else actor
    return match


def resign(match: Match, actor: Actor, expected_version: int) -> Match:
    _check_command(match, actor, expected_version)
    match.state_version += 1
    match.status = "ended"
    match.winner = other(actor)
    match.end_reason = "resign"
    return match
