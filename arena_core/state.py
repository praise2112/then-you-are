"""Pure match state and transitions. No I/O, no framework imports."""

from dataclasses import dataclass, field
from typing import Literal

from arena_judge.schema import Outcome

MatchStatus = Literal["active", "awaiting_judgment", "ended", "abandoned"]
Actor = Literal["p1", "p2"]


class StaleVersionError(Exception):
    """The command carried an expected_version that no longer matches the match."""


@dataclass(frozen=True)
class Turn:
    seq: int
    actor: Actor
    move_text: str
    outcome: Outcome | None = None


@dataclass
class Match:
    id: str
    template_id: str
    template_version: int
    seed: str
    status: MatchStatus = "active"
    state_version: int = 0
    to_move: Actor = "p1"
    turns: list[Turn] = field(default_factory=list)
    strikes: dict[Actor, int] = field(default_factory=lambda: {"p1": 0, "p2": 0})
    winner: Actor | None = None

    @property
    def judged_moves(self) -> int:
        return sum(1 for t in self.turns if t.outcome in ("accept", "fail", "semantic_uncertain"))

    @property
    def last_accepted_move(self) -> str | None:
        for turn in reversed(self.turns):
            if turn.outcome in ("accept", "semantic_uncertain"):
                return turn.move_text
        return None


def other(actor: Actor) -> Actor:
    return "p2" if actor == "p1" else "p1"


def apply_ruling(
    match: Match,
    actor: Actor,
    move_text: str,
    outcome: Outcome,
    expected_version: int,
    move_budget: int,
) -> Match:
    """Record a judged move and advance the match. Sudden death: one fail ends it."""
    if expected_version != match.state_version:
        raise StaleVersionError(f"expected {expected_version}, match is at {match.state_version}")
    if match.status == "ended":
        raise ValueError("match already ended")
    if actor != match.to_move:
        raise ValueError(f"{actor} played out of turn")

    match.turns.append(
        Turn(seq=len(match.turns) + 1, actor=actor, move_text=move_text, outcome=outcome)
    )
    match.state_version += 1

    if outcome in ("deterministic_invalid", "semantic_reject"):
        # Rejects never end a match. The caller decides what a strike count means per actor.
        match.strikes[actor] += 1
        return match

    if outcome == "fail":
        match.status = "ended"
        match.winner = other(actor)
        return match

    match.to_move = other(actor)
    if match.judged_moves >= move_budget:
        # At the cap the winner is decided on points, which live outside this module.
        match.status = "ended"
    return match
