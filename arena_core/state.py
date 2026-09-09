"""Pure match state and transitions. No framework imports, no network."""

import re
from dataclasses import dataclass, field
from typing import Literal

from arena_core.template import Template
from arena_judge.schema import EndReason, Outcome

MatchStatus = Literal["active", "awaiting_judgment", "paused", "ended", "abandoned"]
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
    round_n: int = 1


@dataclass
class Match:
    id: str
    template_id: str
    template_version: int
    cards: list[str]
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
    def seed(self) -> str:
        return self.cards[0]

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

    @property
    def round_n(self) -> int:
        """Showcase only: the round in play, two judged moves per round."""
        return self.judged_moves // 2 + 1

    @property
    def card(self) -> str:
        """Showcase only: the card dealt for the round in play."""
        return self.cards[min(self.round_n, len(self.cards)) - 1]

    def round_turns(self, round_n: int) -> list[Turn]:
        return [t for t in self.turns if t.round_n == round_n and t.outcome in JUDGED]


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
    if normalize(move_text) in {normalize(h) for h in [*match.cards, *match.history]}:
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
    template: Template,
    points: int = 0,
) -> Match:
    """Record a judged or refused move and advance the match by the template's win rule."""
    if template.mode == "showcase" and actor == "p2":
        # The House answers the same card as the player; the engine plays it, never the clock.
        _check_command(match, "p1", expected_version)
    else:
        _check_command(match, actor, expected_version)
    match.state_version += 1
    match.status = "active"

    if outcome in REFUSED:
        # Refusals hand the turn back. What a strike count means is the caller's business.
        match.strikes[actor] += 1
        return match

    round_n = match.round_n if template.mode == "showcase" else len(match.turns) + 1
    match.turns.append(
        Turn(
            seq=len(match.turns) + 1,
            actor=actor,
            move_text=move_text,
            outcome=outcome,
            round_n=round_n,
        )
    )
    if template.win_condition == "sudden_death":
        match.points[actor] += points
        if outcome == "fail":
            match.status = "ended"
            match.winner = other(actor)
            match.end_reason = "sudden_death"
            return match
        match.to_move = other(actor)
    elif outcome != "fail":
        match.points[actor] += points

    if match.judged_moves >= template.move_budget:
        match.status = "ended"
        match.end_reason = "rounds_complete" if template.mode == "showcase" else "move_cap_points"
        p1, p2 = match.points["p1"], match.points["p2"]
        if p1 != p2:
            match.winner = "p1" if p1 > p2 else "p2"
        elif template.tie_policy == "defender_holds":
            match.winner = actor
    return match


def resign(match: Match, actor: Actor, expected_version: int) -> Match:
    _check_command(match, actor, expected_version)
    match.state_version += 1
    match.status = "ended"
    match.winner = other(actor)
    match.end_reason = "resign"
    return match
