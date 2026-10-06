"""Game template: loaded from YAML at boot and linted."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TEMPLATES_DIR = Path(__file__).parent / "templates"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Seed(Strict):
    """A dealt card: an opening form, or a word with a public detail and a judge-only truth."""

    opening_token: str
    opening_emoji: str
    detail: str = ""
    hidden: str = ""

    @property
    def card_text(self) -> str:
        return f"{self.opening_token} ({self.detail})" if self.detail else self.opening_token


class SeedRecipe(Strict):
    """How a generator grows the seed pool: the card shape, the grid it samples and the tests
    a candidate passes."""

    card_shape: Literal["short_form", "sentence"]
    max_words: int = Field(gt=0)
    axes: dict[str, list[str]] = Field(min_length=2)
    tests: list[str] = Field(min_length=1)


class Opening(Strict):
    token: str
    emoji: str


SCORE_MAX = 4


class MoveConstraints(Strict):
    max_chars: int = Field(gt=0)
    prefix: str = ""
    example: str
    hint: str


class DemoMove(Strict):
    actor: Literal["p1", "p2"]
    text: str
    emoji: str
    scores: dict[str, int] | None = None


class LandingOpening(Strict):
    token: str
    examples: list[str] = Field(min_length=1)


class Demo(Strict):
    """One finished round shown on the landing, plus openings a visitor can answer there.
    The second move is always scored; the first may be left unscored."""

    opening: Opening
    moves: list[DemoMove] = Field(min_length=2, max_length=2)
    headline: str
    openings: list[LandingOpening] = Field(min_length=2)


class RubricEntry(Strict):
    """`label` is what players see when the name reads badly on screen; the judge sees the name."""

    name: str
    label: str | None = None
    description: str
    anchors: str
    weight: int = Field(gt=0)


class Criterion(Strict):
    verb: str
    inverse_verb: str
    description: str
    anti_metagaming_clause: str
    judge_notes: list[str] = Field(default_factory=list)


class Example(Strict):
    previous_move: str
    move: str
    gates_note: str
    mechanism: str
    scores: dict[str, int]
    confidence: Literal["clear", "lean", "coin_flip"]
    verdict: Literal["accept", "fail"]


class Tone(Strict):
    commentary_adj: str
    explanation_adj: str


class BadHeadline(Strict):
    text: str
    why: str


class Host(Strict):
    persona_name: str
    tone: Tone
    bite: str
    ruling_generosity: str
    voice_rules: list[str] = Field(min_length=1)
    good_headlines: list[str] = Field(min_length=1)
    bad_headlines: list[BadHeadline] = Field(min_length=1)


class ValidationMessages(Strict):
    empty: str
    too_long: str
    duplicate: str
    nudge: str


class GuessRules(Strict):
    """The call after both bluffs are judged: pick the real entry from among the bluffs."""

    spot_points: int = Field(gt=0)
    fool_points: int = Field(gt=0)
    prompt: str


Mode = Literal["escalation", "showcase"]


class NumPlayers(Strict):
    """How many seats a table of this game may have."""

    min: int = Field(default=2, ge=2)
    max: int = Field(default=2, le=6)


class Labels(Strict):
    """Presentation strings for the slots the frontend fills per game."""

    opening: str
    next_opening: str
    your_opening: str
    compose: str
    compose_waiting: str


class Template(Strict):
    """escalation: each move answers the standing move.
    showcase: both players answer one dealt card per round."""

    schema_version: int
    slug: str
    title: str
    tagline: str
    emblem: str
    accent: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    premise: str
    mode: Mode
    seed_pool: list[Seed] = Field(min_length=1)
    seed_recipe: SeedRecipe | None = None
    demo: Demo
    move_constraints: MoveConstraints
    rubric: list[RubricEntry] = Field(min_length=1)
    criterion: Criterion
    examples: list[Example]
    host: Host
    rules: list[str] = Field(min_length=1)
    labels: Labels
    validation_messages: ValidationMessages
    judge_out_text: str
    opponent_prompt: str
    guess: GuessRules | None = None
    medallions: bool = True
    num_players: NumPlayers = NumPlayers()
    # One move per seat per round; the match ends after the last round.
    rounds_budget: int = Field(gt=0)
    win_condition: Literal["sudden_death", "points_total"]
    tie_policy: Literal["defender_holds", "draw"]
    strikes_before_consequence: int = Field(gt=0)
    default_move: str

    @model_validator(mode="after")
    def lint(self) -> "Template":
        names = [r.name for r in self.rubric]
        if len(set(names)) != len(names):
            raise ValueError("rubric names must be unique")
        for ex in self.examples:
            if set(ex.scores) != set(names):
                raise ValueError(f"example scores {sorted(ex.scores)} do not match rubric {names}")
        if self.demo.moves[1].scores is None:
            raise ValueError("the second demo move needs scores")
        for move in self.demo.moves:
            if move.scores is not None and set(move.scores) != set(names):
                raise ValueError("demo move scores do not match the rubric")
        tokens = {s.opening_token for s in self.seed_pool}
        for opening in self.demo.openings:
            if opening.token not in tokens:
                raise ValueError(f"demo opening {opening.token!r} is not in the seed pool")
        for name, text in (
            ("judge_out_text", self.judge_out_text),
            ("nudge", self.validation_messages.nudge),
        ):
            if "{standing_form}" not in text:
                raise ValueError(f"{name} needs a {{standing_form}} slot")
        for line in self.host.good_headlines:
            if len(line) > 140:
                raise ValueError(f"good headline over 140 chars: {line!r}")
        if self.num_players.min > self.num_players.max:
            raise ValueError("num_players min is above its max")
        if self.mode == "showcase":
            if self.win_condition != "points_total":
                raise ValueError("a showcase game is decided on points_total")
            if len(self.seed_pool) - (self.revealed_card is not None) < self.rounds_budget:
                raise ValueError("the seed pool, less the demo card, must cover every round")
        if self.guess is not None:
            if self.mode != "showcase":
                raise ValueError("only a showcase game has a guess beat")
            if any(not s.hidden for s in self.seed_pool):
                raise ValueError("a guess beat needs a hidden truth on every seed")
        return self

    @property
    def weights(self) -> dict[str, int]:
        return {r.name: r.weight for r in self.rubric}

    def seed_named(self, token: str) -> Seed | None:
        return next((s for s in self.seed_pool if s.opening_token == token), None)

    @property
    def revealed_card(self) -> Seed | None:
        """The demo's opening card when the public demo shows its hidden truth."""
        card = self.seed_named(self.demo.opening.token)
        return card if card is not None and card.hidden else None


def load_template(slug: str, version: int = 1) -> Template:
    return load_template_file(TEMPLATES_DIR / slug / f"v{version}.yaml")


def load_template_file(path: Path) -> Template:
    return Template.model_validate(yaml.safe_load(path.read_text()))


TAGLINE_MAX = 40


def load_templates() -> dict[str, Template]:
    """Every shipped game, keyed by slug, in directory order. A shipped tagline fits a poster."""
    templates = {
        d.name: load_template(d.name) for d in sorted(TEMPLATES_DIR.iterdir()) if d.is_dir()
    }
    for slug, template in templates.items():
        if len(template.tagline) > TAGLINE_MAX:
            raise ValueError(f"{slug}: tagline over {TAGLINE_MAX} characters")
    return templates
