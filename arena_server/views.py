"""Response models. Exported into OpenAPI so the browser client is generated from them."""

from typing import Literal

from pydantic import BaseModel

from arena_core.state import MatchStatus
from arena_judge.schema import (
    EndReason,
    GuessOption,
    GuessView,
    HostPayload,
    Outcome,
    ScoringPayload,
    TurnRejected,
)


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


class GuessRulesView(BaseModel):
    spot_points: int
    fool_points: int
    prompt: str


class NumPlayersView(BaseModel):
    min: int
    max: int


class LabelsView(BaseModel):
    opening: str
    next_opening: str
    your_opening: str
    compose: str
    compose_waiting: str


class TemplateView(BaseModel):
    slug: str
    title: str
    tagline: str
    emblem: str
    accent: str
    premise: str
    mode: Literal["escalation", "showcase"]
    rounds_budget: int
    num_players: NumPlayersView
    rubric: list[RubricView]
    rules: list[str]
    max_chars: int
    move_prefix: str
    move_example: str
    move_hint: str
    score_max: int
    host_name: str
    labels: LabelsView
    guess: GuessRulesView | None
    medallions: bool
    featured: bool
    demo: DemoView


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
    mode: Literal["escalation", "showcase"]
    kind: Literal["house", "friends", "open"]
    status: MatchStatus
    state_version: int
    phase: Literal["write", "guess"]
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
    title: str
    line: str


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
