"""Pure match state and transitions. No framework imports, no network."""

import random
import re
from dataclasses import dataclass, field
from typing import Literal

from arena_core.template import Seed, Template

# open: a table waiting for its seats to fill; nothing is played until it starts.
MatchStatus = Literal["open", "active", "awaiting_judgment", "paused", "ended", "abandoned"]
# A seat: "p1" to "p6", in turn order.
Actor = str
Phase = Literal["write", "guess"]
# "truth", or the seat whose bluff was picked.
Pick = str
Layer1Reason = Literal["empty", "too_long", "duplicate"]
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

JUDGED: tuple[Outcome, ...] = ("accept", "fail", "semantic_uncertain")
STANDING: tuple[Outcome, ...] = ("accept", "semantic_uncertain")
REFUSED: tuple[Outcome, ...] = ("deterministic_invalid", "semantic_reject")
# A turn lost to the clock or to running out of strikes; it answers nothing and scores nothing.
FORFEIT: Outcome = "forfeit"
FORFEITS_TO_ELIMINATE = 2
MAX_SEATS = 6
# The pick recorded for a guesser whose call ran out of time.
NO_PICK = "none"


class IllegalAction(Exception):
    """A command the match does not allow right now. The message is for the player."""


@dataclass(frozen=True)
class Turn:
    seq: int
    actor: Actor
    move_text: str
    outcome: Outcome
    round_n: int = 1
    truth_hit: bool = False
    # What the move was awarded; None when it was never scored.
    points: int | None = None


@dataclass(frozen=True)
class Change:
    """What one transition did. `strikes` is the count a refusal brought the mover to, 0 when
    nothing was refused. `forfeit` is the turn lost to the clock or to the strikes."""

    # The round in play when the transition began.
    round_n: int
    # The judged move it appended.
    turn: Turn | None = None
    strikes: int = 0
    forfeit: Turn | None = None
    call_opened: bool = False
    # Showcase: the round in play was closed, so its answers can be revealed.
    round_closed: bool = False
    ended: bool = False


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
    cards: list[str]
    seats: tuple[Actor, ...] = ("p1", "p2")
    seed_emoji: str = ""
    status: MatchStatus = "active"
    state_version: int = 0
    to_move: Actor = "p1"
    phase: Phase = "write"
    round_n: int = 1
    turns: list[Turn] = field(default_factory=list)
    guesses: list[Guess] = field(default_factory=list)
    # Seats played by people. Only they call in a guess round, and only they run a clock.
    human_seats: tuple[Actor, ...] = ("p1",)
    strikes: dict[Actor, int] = field(default_factory=dict)
    points: dict[Actor, int] = field(default_factory=dict)
    forfeits: dict[Actor, int] = field(default_factory=dict)
    eliminated: list[Actor] = field(default_factory=list)
    winner: Actor | None = None
    end_reason: EndReason | None = None

    def __post_init__(self) -> None:
        # A table still filling, or closed unfilled, may hold one seat; play needs two.
        least = 1 if self.status in ("open", "abandoned") else 2
        if not least <= len(self.seats) <= MAX_SEATS:
            raise ValueError(f"a match seats 2 to {MAX_SEATS} players, not {len(self.seats)}")
        for seat in self.seats:
            self.strikes.setdefault(seat, 0)
            self.points.setdefault(seat, 0)
            self.forfeits.setdefault(seat, 0)

    @property
    def live_seats(self) -> list[Actor]:
        return [s for s in self.seats if s not in self.eliminated]

    @property
    def clocked(self) -> bool:
        """Two or more people share the table, so each turn runs against a clock."""
        return len(self.human_seats) >= 2

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
    def card(self) -> str:
        """Showcase only: the card dealt for the round in play. A round being called is in play."""
        return self.cards[min(self.round_n, len(self.cards)) - 1]

    def round_turns(self, round_n: int) -> list[Turn]:
        return [t for t in self.turns if t.round_n == round_n and t.outcome in JUDGED]

    def has_answered(self, seat: Actor) -> bool:
        """Showcase: the seat has a judged or forfeited answer for the round in play."""
        return any(t.actor == seat and t.round_n == self.round_n for t in self.turns)

    @property
    def round_answered(self) -> bool:
        """Showcase: every live seat has answered the round in play."""
        return all(self.has_answered(s) for s in self.live_seats)

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
        return [
            a
            for a in self.human_seats
            if a in self.live_seats and a not in called and len(self.guess_options(a)) >= 2
        ]


def player(actor: Actor) -> str:
    return f"player{actor[1:]}"


def transcript(match: Match, template: Template, finished_only: bool = False) -> list[str]:
    """The judged moves so far as prompt lines; showcase rounds open with their card."""
    if template.mode == "escalation":
        return [f"{player(t.actor)}: {t.move_text}" for t in match.turns if t.outcome in JUDGED]
    lines = []
    for n, token in enumerate(match.cards, start=1):
        turns = match.round_turns(n)
        if not turns or (finished_only and n >= match.round_n):
            break
        card = template.seed_named(token)
        assert card is not None
        lines.append(f"round {n}, prompt: {card.card_text}")
        lines.extend(f"{player(t.actor)}: {t.move_text}" for t in turns)
    return lines


def deal(
    template: Template,
    rng: random.Random,
    first: Seed | None = None,
    leave_out: Seed | None = None,
) -> list[Seed]:
    """One card for an escalation duel, one per round for a showcase, `first` leading if given
    and `leave_out` never dealt."""
    pool = [c for c in template.seed_pool if c is not leave_out]
    if template.mode == "escalation":
        return [first or rng.choice(pool)]
    rest = [c for c in pool if c is not first]
    cards = rng.sample(rest, template.rounds_budget - bool(first))
    return [first, *cards] if first else cards


def next_seat(match: Match, actor: Actor) -> Actor:
    """The next live seat after `actor` in turn order, wrapping to the first."""
    order = match.seats
    start = order.index(actor)
    for step in range(1, len(order) + 1):
        seat = order[(start + step) % len(order)]
        if seat not in match.eliminated:
            return seat
    return actor


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def refusal(template: Template, move_text: str, taken: list[str]) -> Layer1Reason | None:
    """Why the move is refused before the judge: empty, over the cap, or a repeat of `taken`."""
    if not normalize(move_text):
        return "empty"
    if len(move_text) > template.move_constraints.max_chars:
        return "too_long"
    if normalize(move_text) in {normalize(t) for t in taken}:
        return "duplicate"
    return None


def layer1(template: Template, move_text: str, match: Match) -> Layer1Reason | None:
    """Deterministic checks before the judge, against the cards and the moves that stood."""
    # A showcase round in play is secret, so its answers are no one's history yet.
    earlier = [
        t.move_text
        for t in match.turns
        if t.outcome in STANDING
        and not (template.mode == "showcase" and t.round_n == match.round_n)
    ]
    return refusal(template, move_text, [*match.cards, *earlier])


def repeats_the_round(match: Match, move_text: str) -> bool:
    """Showcase: another seat already gave this exact answer for the round in play."""
    return normalize(move_text) in {
        normalize(t.move_text) for t in match.round_turns(match.round_n)
    }


def weighted_total(scores: dict[str, int], weights: dict[str, int]) -> int:
    return sum(weights.get(name, 0) * value for name, value in scores.items())


def owed(match: Match, template: Template) -> list[Actor]:
    """The seats that owe an answer now: the seat to move, each live seat yet to answer the
    showcase round, or the guessers still to call."""
    if match.status in ("open", "ended", "abandoned"):
        return []
    if match.phase == "guess":
        return match.owed_guesses()
    if template.mode == "escalation":
        return [match.to_move]
    return [s for s in match.live_seats if not match.has_answered(s)]


def model_next(refusals: int, template: Template) -> Literal["write", "default_move", "give_up"]:
    """What a model seat does after `refusals` refused tries at one turn: write again, play the
    template's default move, or give up its seat."""
    if refusals < template.strikes_before_consequence:
        return "write"
    if refusals == template.strikes_before_consequence:
        return "default_move"
    return "give_up"


def _check_open(match: Match, actor: Actor) -> None:
    if match.status in ("ended", "abandoned"):
        raise IllegalAction("match already ended")
    if match.status == "open":
        raise IllegalAction("the table is still filling")
    if actor not in match.live_seats:
        raise IllegalAction("you are out of this match")


def _check_round(match: Match, template: Template, round_n: int | None) -> None:
    if template.mode == "showcase" and round_n is not None and round_n != match.round_n:
        raise IllegalAction("that round is over")


def check_resign(match: Match, actor: Actor) -> None:
    """Raises IllegalAction unless the seat may leave the match now."""
    _check_open(match, actor)


def check_guess(match: Match, actor: Actor, template: Template, round_n: int | None = None) -> None:
    """Raises IllegalAction unless the seat owes a call now. `round_n` is the round the command
    was sent for, when it says."""
    _check_open(match, actor)
    _check_round(match, template, round_n)
    if match.phase != "guess" or actor not in match.owed_guesses():
        raise IllegalAction("no call to make")


def check_move(match: Match, actor: Actor, template: Template, round_n: int | None = None) -> None:
    """Raises IllegalAction unless the seat may answer now: in escalation one seat at a time, in
    showcase every live seat once a round. `round_n` is as for check_guess."""
    _check_open(match, actor)
    _check_round(match, template, round_n)
    if match.phase != "write":
        raise IllegalAction("not your move")
    if template.mode == "showcase":
        if match.has_answered(actor):
            raise IllegalAction("you already answered this round")
    elif actor != match.to_move:
        raise IllegalAction("not your move")


def _change(
    match: Match,
    template: Template,
    round_n: int,
    phase: Phase,
    turn: Turn | None = None,
    strikes: int = 0,
    forfeit: Turn | None = None,
) -> Change:
    """The Change for a transition that began in `round_n` and `phase`."""
    return Change(
        round_n=round_n,
        turn=turn,
        strikes=strikes,
        forfeit=forfeit,
        call_opened=phase == "write" and match.phase == "guess",
        round_closed=template.mode == "showcase" and match.round_n != round_n,
        ended=match.status == "ended",
    )


def apply_ruling(
    match: Match,
    actor: Actor,
    move_text: str,
    outcome: Outcome,
    template: Template,
    points: int = 0,
    truth_hit: bool = False,
) -> Change:
    """Record a judged or refused move and advance the match by the template's win rule.
    `points` is the move's weighted score; a fail is awarded none."""
    check_move(match, actor, template)
    round_n, phase = match.round_n, match.phase
    match.state_version += 1
    match.status = "active"

    if outcome in REFUSED:
        # The turn comes back, unless a person at a clocked table has run out of strikes.
        match.strikes[actor] += 1
        strikes = match.strikes[actor]
        forfeit = None
        struck_out = strikes >= template.strikes_before_consequence
        if match.clocked and actor in match.human_seats and struck_out:
            forfeit = _forfeit(match, actor, template)
        return _change(match, template, round_n, phase, strikes=strikes, forfeit=forfeit)

    earned = 0 if outcome == "fail" else points
    turn = Turn(
        seq=len(match.turns) + 1,
        actor=actor,
        move_text=move_text,
        outcome=outcome,
        round_n=match.round_n,
        truth_hit=truth_hit,
        points=earned,
    )
    match.turns.append(turn)
    match.strikes[actor] = 0
    match.points[actor] += earned
    # On a sudden-death fail the mover is out; the next seat answers the same standing form.
    sudden = outcome == "fail" and template.win_condition == "sudden_death"
    _move_on(match, template, actor, "sudden_death" if sudden else None)
    return _change(match, template, round_n, phase, turn=turn)


def forfeit_turn(match: Match, seat: Actor, template: Template) -> Change:
    """The seat loses its turn to the clock. A second forfeit puts it out."""
    check_move(match, seat, template)
    round_n, phase = match.round_n, match.phase
    turn = _forfeit(match, seat, template)
    return _change(match, template, round_n, phase, forfeit=turn)


def _forfeit(match: Match, seat: Actor, template: Template) -> Turn:
    match.state_version += 1
    match.status = "active"
    match.strikes[seat] = 0
    turn = Turn(
        seq=len(match.turns) + 1,
        actor=seat,
        move_text="",
        outcome=FORFEIT,
        round_n=match.round_n,
    )
    match.turns.append(turn)
    match.forfeits[seat] += 1
    knocked = match.forfeits[seat] >= FORFEITS_TO_ELIMINATE
    _move_on(match, template, seat, "forfeit" if knocked else None)
    return turn


def _move_on(match: Match, template: Template, actor: Actor, out: EndReason | None) -> None:
    """After the actor's turn: puts it out when `out` gives a reason, then passes the turn or
    closes the round."""
    following = next_seat(match, actor)
    if out is not None:
        match.eliminated.append(actor)
        if _last_seat_standing(match, out):
            return
    if template.mode == "showcase":
        if match.round_answered:
            _close_round(match, template, actor)
        return
    _pass_turn(match, actor, following)
    _end_on_budget(match, template, actor)


def _last_seat_standing(match: Match, reason: EndReason) -> bool:
    if len(match.live_seats) != 1:
        return False
    match.status = "ended"
    match.winner = match.live_seats[0]
    match.end_reason = reason
    return True


def _pass_turn(match: Match, actor: Actor, following: Actor) -> None:
    """Escalation: a turn that wraps back to an earlier seat starts the next round."""
    if match.seats.index(following) <= match.seats.index(actor):
        match.round_n += 1
    match.to_move = following


def _close_round(match: Match, template: Template, last_actor: Actor) -> None:
    """Showcase: holds the round open for calls when someone owes one, else deals the next."""
    if template.guess is not None:
        match.phase = "guess"
        owed = match.owed_guesses()
        if owed:
            match.to_move = owed[0]
            return
        match.phase = "write"
    _next_round(match, template, last_actor)


def _next_round(match: Match, template: Template, last_actor: Actor) -> None:
    match.round_n += 1
    match.to_move = match.live_seats[0]
    _end_on_budget(match, template, last_actor)


def apply_guess(match: Match, actor: Actor, picked: Pick, template: Template) -> Change:
    """Record a call. The truth pays the guesser; a bluff pays the player who wrote it."""
    check_guess(match, actor, template)
    if picked not in match.guess_options(actor):
        raise IllegalAction("that entry is not on the table")
    assert template.guess is not None
    round_n, phase = match.round_n, match.phase
    match.state_version += 1
    if picked == "truth":
        awarded_to = actor
        points = template.guess.spot_points
    else:
        awarded_to = picked
        points = template.guess.fool_points
    match.points[awarded_to] += points
    match.guesses.append(Guess(match.round_n, actor, picked, points, awarded_to))
    _after_guess(match, template, actor)
    return _change(match, template, round_n, phase)


def skip_guess(match: Match, actor: Actor, template: Template) -> Change:
    """The guesser ran out of time: the call is recorded as no pick and pays nobody."""
    check_guess(match, actor, template)
    round_n, phase = match.round_n, match.phase
    match.state_version += 1
    match.guesses.append(Guess(match.round_n, actor, NO_PICK, 0, actor))
    _after_guess(match, template, actor)
    return _change(match, template, round_n, phase)


def _after_guess(match: Match, template: Template, actor: Actor) -> None:
    owed = match.owed_guesses()
    if owed:
        match.to_move = owed[0]
        return
    match.phase = "write"
    _next_round(match, template, actor)


def _end_on_budget(match: Match, template: Template, last_actor: Actor) -> None:
    """Ends after the last round on points. Only live seats can win; a tie at the top is a
    draw, or under defender_holds goes to the tied seat that moved last."""
    if match.round_n <= template.rounds_budget:
        return
    match.status = "ended"
    match.end_reason = "rounds_complete" if template.mode == "showcase" else "move_cap_points"
    live = match.live_seats
    top = max(match.points[s] for s in live)
    leaders = [s for s in live if match.points[s] == top]
    if len(leaders) == 1:
        match.winner = leaders[0]
    elif template.tie_policy == "defender_holds":
        match.winner = next(
            (t.actor for t in reversed(match.turns) if t.actor in leaders), last_actor
        )


def resign(match: Match, actor: Actor, template: Template) -> Change:
    """The seat leaves the match. With one seat left it wins; otherwise play goes on."""
    check_resign(match, actor)
    round_n, phase = match.round_n, match.phase
    match.state_version += 1
    match.eliminated.append(actor)
    if _last_seat_standing(match, "resign"):
        return _change(match, template, round_n, phase)
    if template.mode == "escalation":
        if match.to_move == actor:
            _pass_turn(match, actor, next_seat(match, actor))
            _end_on_budget(match, template, actor)
    elif match.phase == "write" and match.round_answered:
        _close_round(match, template, actor)
    elif match.phase == "guess":
        _after_guess(match, template, actor)
    return _change(match, template, round_n, phase)
