"""Response models. Exported into OpenAPI so the browser client is generated from them."""

from typing import Literal

from pydantic import BaseModel

from arena_core.state import EndReason, MatchStatus, Outcome, Phase, ResultKind
from arena_core.template import SCORE_MAX, GuessRules, Labels, Mode, NumPlayers, Template
from arena_judge.schema import HostPayload, ScoringPayload
from arena_server.events import GuessOption, GuessView, TurnRejected

TableKind = Literal["house", "friends", "open"]


class RubricView(BaseModel):
    name: str
    label: str | None
    description: str
    max_points: int


class DemoOpening(BaseModel):
    token: str
    emoji: str
    detail: str
    reveal: str


class DemoPoints(BaseModel):
    name: str
    earned: int
    max_points: int


class DemoMoveView(BaseModel):
    actor: str
    text: str
    emoji: str
    points: list[DemoPoints] | None


class LandingOpeningView(BaseModel):
    token: str
    emoji: str
    detail: str
    examples: list[str]


class DemoView(BaseModel):
    opening: DemoOpening
    moves: list[DemoMoveView]
    headline: str
    openings: list[LandingOpeningView]


class TemplateView(BaseModel):
    slug: str
    title: str
    tagline: str
    emblem: str
    accent: str
    premise: str
    mode: Mode
    rounds_budget: int
    num_players: NumPlayers
    rubric: list[RubricView]
    rules: list[str]
    max_chars: int
    move_prefix: str
    move_example: str
    move_hint: str
    score_max: int
    host_name: str
    labels: Labels
    guess: GuessRules | None
    medallions: bool
    featured: bool
    demo: DemoView


def template_view(template: Template, featured: bool) -> TemplateView:
    """What the browser may see of a game: no examples and nothing written for the judge."""

    def points(scores: dict[str, int]) -> list[DemoPoints]:
        return [
            DemoPoints(
                name=r.name, earned=scores[r.name] * r.weight, max_points=r.weight * SCORE_MAX
            )
            for r in template.rubric
        ]

    demo = template.demo
    card = template.seed_named(demo.opening.token)
    return TemplateView(
        slug=template.slug,
        title=template.title,
        tagline=template.tagline,
        emblem=template.emblem,
        accent=template.accent,
        premise=template.premise.strip(),
        mode=template.mode,
        rounds_budget=template.rounds_budget,
        num_players=template.num_players,
        rubric=[
            RubricView(
                name=r.name,
                label=r.label,
                description=r.description,
                max_points=r.weight * SCORE_MAX,
            )
            for r in template.rubric
        ],
        rules=[r.strip() for r in template.rules],
        max_chars=template.move_constraints.max_chars,
        move_prefix=template.move_constraints.prefix,
        move_example=template.move_constraints.example,
        move_hint=template.move_constraints.hint,
        score_max=SCORE_MAX,
        host_name=template.host.persona_name,
        labels=template.labels,
        guess=template.guess,
        medallions=template.medallions,
        featured=featured,
        demo=DemoView(
            opening=DemoOpening(
                token=demo.opening.token,
                emoji=demo.opening.emoji,
                detail=card.detail if card else "",
                reveal=card.hidden if card else "",
            ),
            moves=[
                DemoMoveView(
                    actor=m.actor,
                    text=m.text,
                    emoji=m.emoji,
                    points=None if m.scores is None else points(m.scores),
                )
                for m in demo.moves
            ],
            headline=demo.headline,
            openings=[
                LandingOpeningView(
                    token=o.token,
                    emoji=seed.opening_emoji,
                    detail=seed.detail,
                    examples=o.examples,
                )
                for o in demo.openings
                if (seed := template.seed_named(o.token)) is not None
            ],
        ),
    )


class TurnView(BaseModel):
    seq: int
    round_n: int
    actor: str
    move_text: str
    outcome: Outcome
    scoring: ScoringPayload | None
    host: HostPayload | None
    points: int | None
    # The model that played this House move in the House's place, when it failed.
    played_by: str | None = None


class RoundView(BaseModel):
    """A dealt card. Options are on the table while the round is being called; the truth
    and the calls are filled in once the round is revealed."""

    round_n: int
    token: str
    emoji: str
    detail: str
    truth: str | None
    options: list[GuessOption] = []
    guesses: list[GuessView] = []


class SeatView(BaseModel):
    """One seat at the table, in turn order. Points leave out the round still being played."""

    seat: str
    kind: Literal["human", "model"]
    display_name: str
    # The model behind a House seat, for the replay billing.
    model: str | None
    points: int
    eliminated: bool
    # Showcase: this seat has written (or called) for the round in play.
    answered: bool


class MatchSnapshot(BaseModel):
    id: str
    template_id: str
    title: str
    mode: Mode
    kind: TableKind
    status: MatchStatus
    state_version: int
    # Open the match's event stream after this id to hear every change the snapshot lacks.
    event_id: str
    phase: Phase
    seed_token: str
    seed_emoji: str
    rounds: list[RoundView]
    round_in_play: int
    seats: list[SeatView]
    seats_wanted: int
    # The viewer's seat, or None for a spectator.
    your_seat: str | None
    # Shown to seated players only, while the table can still be joined.
    invite_code: str | None
    # When a table still filling closes unfilled.
    closes_at: str | None
    turn_deadline: str | None
    # How long the running clock was set for, to draw how much of it is left.
    clock_seconds: int | None
    to_move: str
    winner: str | None
    end_reason: EndReason | None
    judged_moves: int
    # Showcase: why the viewer's last answer this round came back, for that viewer only.
    returned: TurnRejected | None = None
    transcript: list[TurnView]
    created_at: str
    is_public: bool
    is_yours: bool = False


class AccountView(BaseModel):
    id: str
    providers: list[str]
    display_name: str
    avatar_url: str
    streak: int
    best_streak: int


class OpenDuel(BaseModel):
    """A duel the session (or its account) can walk back into."""

    id: str
    template_id: str
    title: str
    mode: Mode
    # Seats still empty while the table fills; null once play has begun.
    waiting_for: int | None
    round_n: int
    rounds_budget: int
    # Showcase: the card in play. Escalation: the form that stands, prefix and all.
    card: str
    # Showcase: the seat owes a call. Escalation: the seat is to move at a clocked table.
    your_turn: bool


class SessionView(BaseModel):
    stage_name: str
    list_duels: bool
    account: AccountView | None = None
    providers: list[str] = []
    open_duels: list[OpenDuel] = []


class StandingView(BaseModel):
    rank: int
    account_id: str
    display_name: str
    avatar_url: str
    wins: int
    played: int


class BoardView(BaseModel):
    slug: str
    title: str
    emblem: str
    accent: str
    standings: list[StandingView]


class BoardSummary(BaseModel):
    """One card on the standings index: the game, who leads it, how many are ranked."""

    slug: str
    title: str
    emblem: str
    accent: str
    ranked: int
    leader: StandingView | None


class TableView(BaseModel):
    """An open table in the lobby, waiting for players."""

    id: str
    template_id: str
    title: str
    emblem: str
    host_name: str
    invite_code: str
    seats_taken: int
    seats_wanted: int
    created_at: str


class StageView(BaseModel):
    """Public matches in play, and how many duels have finished."""

    live: list[MatchSnapshot]
    duels_played: int


class Replay(MatchSnapshot):
    result_kind: ResultKind
    share_text: str
    highlight_seq: int | None
    is_curated: bool


class GameRecord(BaseModel):
    slug: str
    title: str
    played: int
    won: int
    drawn: int
    best_streak: int
    rank: int | None


class BadgeCount(BaseModel):
    name: str
    count: int


class DuelRow(BaseModel):
    id: str
    title: str
    created_at: str
    status: MatchStatus
    length: str
    result: str
    won: bool | None
    is_public: bool
    # Who else sat at the table, in seat order.
    against: str


class ProfileView(BaseModel):
    """A player's public programme. Open, private and closed duels appear only to the owner."""

    id: str
    display_name: str
    avatar_url: str
    since: str
    played: int
    won: int
    streak: int
    best_streak: int
    records: list[GameRecord]
    badges: list[BadgeCount]
    duels: list[DuelRow]
    # Games against people: exhibition, never on the record or the streak.
    people: list[DuelRow]
    best: list[Replay]
    is_yours: bool
