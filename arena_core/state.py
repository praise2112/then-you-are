"""Pure match state and transitions. No framework imports, no network."""

import random
import re
from dataclasses import dataclass, field
from typing import Literal

from arena_core.template import Seed, Template
from arena_judge.schema import EndReason, Outcome

MatchStatus = Literal["active", "awaiting_judgment", "paused", "ended", "abandoned"]
Actor = Literal["p1", "p2"]
Phase = Literal["write", "guess"]
Pick = Literal["truth", "p1", "p2"]
Layer1Reason = Literal["empty", "too_long", "duplicate"]

JUDGED: tuple[Outcome, ...] = ("accept", "fail", "semantic_uncertain")
STANDING: tuple[Outcome, ...] = ("accept", "semantic_uncertain")
REFUSED: tuple[Outcome, ...] = ("deterministic_invalid", "semantic_reject")
PLAYERS: tuple[Actor, ...] = ("p1", "p2")


class StaleVersionError(Exception):
    """The command carried an expected_version that no longer matches the match."""


@dataclass(frozen=True)
class Turn:
    seq: int
    actor: Actor
    move_text: str
    outcome: Outcome
    round_n: int = 1
    truth_hit: bool = False


@dataclass(frozen=True)
class Guess:
    """One call in a showcase round: the guesser picked the truth or another player's bluff."""

    round_n: int
    actor: Actor
    picked: Pick
    points: int
    awarded_to: Actor


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
    phase: Phase = "write"
    turns: list[Turn] = field(default_factory=list)
    guesses: list[Guess] = field(default_factory=list)
    # Who calls the real entry in a guess round. A model opponent writes but never guesses.
    guessers: tuple[Actor, ...] = ("p1",)
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
        """Showcase only: the round in play. A round being guessed on is still in play."""
        judged_rounds = self.judged_moves // len(PLAYERS)
        return judged_rounds if self.phase == "guess" else judged_rounds + 1

    @property
    def card(self) -> str:
        """Showcase only: the card dealt for the round in play."""
        return self.cards[min(self.round_n, len(self.cards)) - 1]

    def round_turns(self, round_n: int) -> list[Turn]:
        return [t for t in self.turns if t.round_n == round_n and t.outcome in JUDGED]

    def round_guesses(self, round_n: int) -> list[Guess]:
        return [g for g in self.guesses if g.round_n == round_n]

    def guess_options(self, guesser: Actor) -> list[Pick]:
        """What the guesser may pick this round: the truth and every other bluff that missed it."""
        bluffs: list[Pick] = [
            t.actor
            for t in self.round_turns(self.round_n)
            if t.actor != guesser and not t.truth_hit
        ]
        return ["truth", *bluffs]

    def owed_guesses(self) -> list[Actor]:
        """Guessers still to call this round. One option is no choice, so it owes nothing."""
        called = {g.actor for g in self.round_guesses(self.round_n)}
        return [a for a in self.guessers if a not in called and len(self.guess_options(a)) >= 2]


def player(actor: Actor) -> str:
    return "player1" if actor == "p1" else "player2"


def transcript(match: Match, template: Template, finished_only: bool = False) -> list[str]:
    """The judged moves so far as prompt lines; showcase rounds open with their card."""
    if template.mode == "escalation":
        return [f"{player(t.actor)}: {t.move_text}" for t in match.turns]
    lines = []
    for n, token in enumerate(match.cards, start=1):
        turns = match.round_turns(n)
        if not turns or (finished_only and len(turns) < 2):
            break
        card = template.seed_named(token)
        assert card is not None
        lines.append(f"round {n}, prompt: {card.card_text}")
        lines.extend(f"{player(t.actor)}: {t.move_text}" for t in turns)
    return lines


def deal(template: Template, rng: random.Random, first: Seed | None = None) -> list[Seed]:
    """One card for an escalation duel, one per round for a showcase, `first` leading if given."""
    if template.mode == "escalation":
        return [first or rng.choice(template.seed_pool)]
    rest = [c for c in template.seed_pool if c is not first]
    cards = rng.sample(rest, template.move_budget // 2 - bool(first))
    return [first, *cards] if first else cards


def other(actor: Actor) -> Actor:
    return "p2" if actor == "p1" else "p1"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def layer1(template: Template, move_text: str, match: Match) -> Layer1Reason | None:
    """Deterministic checks before the judge: empty, over the cap, exact duplicate."""
    if not normalize(move_text):
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
    truth_hit: bool = False,
) -> Match:
    """Record a judged or refused move and advance the match by the template's win rule."""
    if template.mode == "showcase" and actor == "p2":
        # The House answers the same card as the player; the engine plays it, never the clock.
        _check_command(match, "p1", expected_version)
    else:
        _check_command(match, actor, expected_version)
    if match.phase != "write":
        raise ValueError("the round is being guessed on")
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
            truth_hit=truth_hit,
        )
    )
    if outcome != "fail":
        match.points[actor] += points
    if template.win_condition == "sudden_death":
        if outcome == "fail":
            match.status = "ended"
            match.winner = other(actor)
            match.end_reason = "sudden_death"
            return match
        match.to_move = other(actor)

    if template.mode == "showcase" and len(match.round_turns(round_n)) == len(PLAYERS):
        _open_guessing(match, template)
        if match.phase == "guess":
            return match
    _end_on_budget(match, template, actor)
    return match


def _open_guessing(match: Match, template: Template) -> None:
    """Holds the round open for calls when the template has a guess beat and someone owes one."""
    if template.guess is None:
        return
    # round_n only points at the finished round once the phase is guess.
    match.phase = "guess"
    owed = match.owed_guesses()
    if not owed:
        match.phase = "write"
        return
    match.to_move = owed[0]


def apply_guess(
    match: Match, actor: Actor, picked: Pick, expected_version: int, template: Template
) -> Match:
    """Record a call. The truth pays the guesser; a bluff pays the player who wrote it."""
    if expected_version != match.state_version:
        raise StaleVersionError(f"expected {expected_version}, match is at {match.state_version}")
    if match.status in ("ended", "abandoned"):
        raise ValueError("match already ended")
    if match.phase != "guess" or actor not in match.owed_guesses():
        raise ValueError(f"{actor} has no call to make")
    if picked not in match.guess_options(actor):
        raise ValueError("that entry is not on the table")
    assert template.guess is not None
    match.state_version += 1
    if picked == "truth":
        awarded_to: Actor = actor
        points = template.guess.spot_points
    else:
        awarded_to = picked
        points = template.guess.fool_points
    match.points[awarded_to] += points
    match.guesses.append(Guess(match.round_n, actor, picked, points, awarded_to))
    owed = match.owed_guesses()
    if owed:
        match.to_move = owed[0]
        return match
    match.phase = "write"
    match.to_move = "p1"
    _end_on_budget(match, template, actor)
    return match


def _end_on_budget(match: Match, template: Template, last_actor: Actor) -> None:
    if match.judged_moves < template.move_budget:
        return
    match.status = "ended"
    match.end_reason = "rounds_complete" if template.mode == "showcase" else "move_cap_points"
    p1, p2 = match.points["p1"], match.points["p2"]
    if p1 != p2:
        match.winner = "p1" if p1 > p2 else "p2"
    elif template.tie_policy == "defender_holds":
        match.winner = last_actor


def resign(match: Match, actor: Actor, expected_version: int) -> Match:
    _check_command(match, actor, expected_version)
    match.state_version += 1
    match.status = "ended"
    match.winner = other(actor)
    match.end_reason = "resign"
    return match
