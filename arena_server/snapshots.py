"""What a viewer may see of a match: the snapshot, its rounds and seats, the call's options,
the replay's share text and highlight, and why an answer came back."""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from arena_core.state import FINISHED, STANDING, Actor, Match
from arena_core.template import Seed, Template
from arena_judge.schema import HostPayload
from arena_server.db import Pool
from arena_server.events import GuessOption, GuessView, TurnRejected
from arena_server.store import UNFILLED_WINDOW, MatchError, Record, SeatRow, last_refusal
from arena_server.views import MatchSnapshot, RoundView, SeatView, TurnView

# Human seats get a clock only when two or more humans share the table.
TURN_CLOCK = timedelta(seconds=120)
WRITE_CLOCK = timedelta(seconds=180)
CALL_CLOCK = timedelta(seconds=60)
REPEAT_TEXT = "Someone at the table already wrote exactly that. Try another."


def build_snapshot(
    match: Match,
    template: Template,
    rec: Record,
    viewer: str | None,
    returned: TurnRejected | None,
    opponent_name: str,
    stand_in: Callable[[str | None], str | None],
    event_id: str,
) -> MatchSnapshot:
    """The match as the viewer's seat, or a spectator when viewer is None, may see it.
    stand_in names the model that played a turn in the House's place."""
    rows = [
        t for t in rec.turn_rows if visible_to(match, template, viewer, t["round_n"], t["actor"])
    ]
    return MatchSnapshot(
        id=match.id,
        template_id=match.template_id,
        title=template.title,
        mode=template.mode,
        kind=rec.kind,
        status=match.status,
        state_version=match.state_version,
        event_id=event_id,
        phase=match.phase,
        seed_token=match.seed,
        seed_emoji=match.seed_emoji if template.mode == "escalation" else "",
        rounds=rounds(match, template, viewer),
        round_in_play=match.round_n,
        seats=seat_views(match, template, rec, opponent_name),
        seats_wanted=rec.seats_wanted,
        your_seat=viewer,
        invite_code=rec.invite_code if viewer and match.status == "open" else None,
        closes_at=(rec.created_at + UNFILLED_WINDOW).isoformat()
        if match.status == "open"
        else None,
        turn_deadline=rec.turn_deadline.isoformat() if rec.turn_deadline else None,
        clock_seconds=int(clock_length(match, template).total_seconds())
        if rec.turn_deadline
        else None,
        to_move=match.to_move,
        winner=match.winner,
        end_reason=match.end_reason,
        judged_moves=match.judged_moves,
        transcript=[
            TurnView(
                seq=t["seq"],
                round_n=t["round_n"],
                actor=t["actor"],
                move_text=t["move_text"],
                outcome=t["outcome"],
                scoring=t["scoring"],
                host=t["host"],
                points=t["points"],
                played_by=stand_in(t["model_ref"]),
            )
            for t in rows
        ],
        returned=returned,
        created_at=rec.created_at.isoformat(),
        is_public=rec.is_public,
        is_yours=viewer is not None,
    )


def display_name(row: SeatRow, rec: Record) -> str:
    if row.kind == "human":
        return row.stage_name or "Challenger"
    houses = sorted(rec.models, key=lambda seat: int(seat[1:]))
    n = houses.index(row.seat) + 1
    return "The House" if n == 1 else f"The House {n}"


def shows_live(template: Template) -> bool:
    """The table sees each turn as it is played, one seat at a time. Otherwise every seat answers
    a round at once, and the round stays hidden until it is revealed."""
    return template.mode == "escalation"


def hides_round(match: Match, template: Template) -> bool:
    """The round in play stays off the wire until it is revealed."""
    return not shows_live(template) and match.status not in FINISHED


def visible_to(
    match: Match, template: Template, viewer: str | None, round_n: int, actor: str
) -> bool:
    """Whether the viewer's seat, or the whole table when None, may see a turn or a call. The
    round in play shows a seat only its own answer, and none while it is called."""
    if not hides_round(match, template) or round_n != match.round_n:
        return True
    return match.phase == "write" and actor == viewer


def shown_points(match: Match, template: Template, rec: Record) -> dict[str, int]:
    """Seat totals as the whole table may see them."""
    shown = dict(match.points)
    for t in rec.turn_rows:
        if not visible_to(match, template, None, t["round_n"], t["actor"]):
            shown[t["actor"]] -= t["points"] or 0
    for g in match.guesses:
        if not visible_to(match, template, None, g.round_n, g.actor):
            shown[g.awarded_to] -= g.points
    return shown


def in_call(match: Match, round_n: int) -> bool:
    return match.phase == "guess" and round_n == match.round_n


def rounds(match: Match, template: Template, viewer: str | None) -> list[RoundView]:
    if template.mode == "escalation":
        return []
    over = match.status in FINISHED
    owes = viewer is not None and viewer in match.owed_guesses()
    views = []
    for n, token in enumerate(match.cards[: match.round_n], start=1):
        card = template.seed_named(token)
        assert card is not None
        calling = in_call(match, n)
        revealed = (n < match.round_n or over) and not calling
        views.append(
            RoundView(
                round_n=n,
                token=token,
                emoji=card.opening_emoji if revealed else "",
                detail=card.detail,
                truth=card.hidden if revealed else None,
                options=call_options(match, card, viewer) if calling and owes and viewer else [],
                guesses=guess_views(match, n) if revealed else [],
            )
        )
    return views


def call_entries(match: Match, card: Seed, guesser: Actor) -> dict[str, tuple[str, str]]:
    """The entries on the table for a guesser, keyed by a hash that says nothing about them."""
    bluffs = {t.actor: t.move_text for t in match.round_turns(match.round_n)}
    table = {}
    for pick in match.guess_options(guesser):
        text = entry_case(card.hidden if pick == "truth" else bluffs[pick])
        key = hashlib.sha256(f"{match.id}:{match.round_n}:{text}".encode()).hexdigest()[:8]
        table[key] = (pick, text)
    return table


def call_options(match: Match, card: Seed, guesser: Actor) -> list[GuessOption]:
    table = call_entries(match, card, guesser)
    return [GuessOption(key=key, text=table[key][1]) for key in sorted(table)]


def resolve_pick(match: Match, card: Seed, guesser: Actor, key: str) -> str:
    entry = call_entries(match, card, guesser).get(key)
    if entry is None:
        raise MatchError(422, "that entry is not on the table")
    return entry[0]


def guess_views(match: Match, round_n: int) -> list[GuessView]:
    return [
        GuessView(actor=g.actor, picked=g.picked, points=g.points, awarded_to=g.awarded_to)
        for g in match.round_guesses(round_n)
    ]


def seat_views(match: Match, template: Template, rec: Record, opponent_name: str) -> list[SeatView]:
    shown = shown_points(match, template, rec)
    if match.phase == "guess":
        # A seat that owes no call (a model seat, or a guesser with no choice) is done.
        answered = set(match.seats) - set(match.owed_guesses())
    else:
        answered = {t.actor for t in match.turns if t.round_n == match.round_n}
    return [
        SeatView(
            seat=row.seat,
            kind=row.kind,
            display_name=display_name(row, rec),
            model=opponent_name if row.kind == "model" else None,
            points=shown[row.seat],
            eliminated=row.seat in match.eliminated,
            answered=template.mode == "showcase" and (row.seat in answered or row.submitted),
        )
        for row in rec.seats
    ]


async def returned_answer(
    pool: Pool, match: Match, template: Template, rec: Record, viewer: str
) -> TurnRejected | None:
    """Showcase: why the viewer's last answer this round came back, while it still owes one."""
    if match.phase != "write" or viewer in match.eliminated or match.has_answered(viewer):
        return None
    if rec.row(viewer).submitted:
        return None
    row = await last_refusal(pool, match.id, viewer, match.round_n)
    if row is None:
        return None
    if row["outcome"] == "semantic_reject" and row["host"]:
        host = HostPayload.model_validate(row["host"])
        return rejection(
            match,
            template,
            viewer,
            match.strikes[viewer],
            "semantic_reject",
            host.headline,
            host.quotable_line,
        )
    reason = row["layer1_result"]
    if reason == "repeat":
        text = REPEAT_TEXT
    elif reason in ("empty", "too_long", "duplicate"):
        text = getattr(template.validation_messages, reason)
    else:
        return None
    return rejection(match, template, viewer, match.strikes[viewer], "deterministic_invalid", text)


def rejection(
    match: Match,
    template: Template,
    actor: Actor,
    strikes: int,
    outcome: Literal["deterministic_invalid", "semantic_reject"],
    reason_text: str,
    nudge: str | None = None,
) -> TurnRejected:
    if strikes < template.strikes_before_consequence:
        nudge = None
    elif nudge is None:
        target = match.card if template.mode == "showcase" else match.standing_form
        nudge = template.validation_messages.nudge.format(standing_form=target)
    return TurnRejected(
        seat=actor,
        outcome=outcome,
        reason_text=reason_text,
        strikes=strikes,
        state_version=match.state_version,
        nudge_text=nudge,
    )


def clock_length(match: Match, template: Template) -> timedelta:
    if template.mode == "escalation":
        return TURN_CLOCK
    return CALL_CLOCK if match.phase == "guess" else WRITE_CLOCK


def highlight_seq(snap: MatchSnapshot) -> int | None:
    winner_moves = [
        t
        for t in snap.transcript
        if t.actor == snap.winner and t.points is not None and t.outcome != "fail"
    ]
    best = max(winner_moves, key=lambda t: t.points or 0, default=None)
    return best.seq if best else None


def share_text(snap: MatchSnapshot, public_base_url: str) -> str:
    """Written from the seat of the table's creator."""
    link = f"{public_base_url}/r/{snap.id}"
    owner = next(s for s in snap.seats if s.kind == "human")
    result = "won" if snap.winner == owner.seat else "lost" if snap.winner else "drew"
    game = "a duel" if len(snap.seats) == 2 else f"a {len(snap.seats)}-player game"
    if snap.mode == "showcase":
        best_other = max((s.points for s in snap.seats if s.seat != owner.seat), default=0)
        cards = " ".join(r.emoji for r in snap.rounds if r.emoji)
        return f"I {result} {game} of {snap.title}, {owner.points} to {best_other}. {cards} {link}"
    chain = [snap.seed_emoji] + [
        t.host.generated_emoji for t in snap.transcript if t.host and t.outcome in STANDING
    ]
    return (
        f"I {result} {game} of {snap.title} in {snap.judged_moves} moves. {'→'.join(chain)} {link}"
    )


def entry_case(text: str) -> str:
    """Dictionary casing for an entry on the table: lowercase start, no closing full stop."""
    text = text.strip().rstrip(".").strip()
    if len(text) > 1 and text[1].islower():
        text = text[0].lower() + text[1:]
    return text
