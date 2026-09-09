"""Game template: loaded from YAML at boot, linted, and projected for players."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TEMPLATES_DIR = Path(__file__).parent / "templates"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Seed(Strict):
    opening_token: str
    opening_emoji: str


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


class LandingOpening(Strict):
    token: str
    examples: list[str] = Field(min_length=1)


class Demo(Strict):
    """One finished round shown on the landing, plus openings a visitor can answer there."""

    opening: Opening
    moves: list[DemoMove] = Field(min_length=2, max_length=2)
    headline: str
    scores: dict[str, int]
    openings: list[LandingOpening] = Field(min_length=1)


class RubricEntry(Strict):
    name: str
    description: str
    weight: int = Field(gt=0)


class Criterion(Strict):
    verb: str
    inverse_verb: str
    description: str
    anti_metagaming_clause: str


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


class Host(Strict):
    persona_name: str
    tone: Tone
    bite: str
    ruling_generosity: str


class ValidationMessages(Strict):
    empty: str
    too_long: str
    duplicate: str
    nudge: str


class Template(Strict):
    schema_version: int
    slug: str
    title: str
    tagline: str
    premise: str
    seed_pool: list[Seed] = Field(min_length=1)
    demo: Demo
    move_constraints: MoveConstraints
    rubric: list[RubricEntry] = Field(min_length=1)
    criterion: Criterion
    examples: list[Example]
    host: Host
    rules_text: str
    validation_messages: ValidationMessages
    judge_out_text: str
    move_budget: int = Field(gt=0)
    win_condition: Literal["sudden_death"]
    tie_policy: Literal["defender_holds"]
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
        if set(self.demo.scores) != set(names):
            raise ValueError("demo scores do not match the rubric")
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
        return self

    @property
    def weights(self) -> dict[str, int]:
        return {r.name: r.weight for r in self.rubric}

    def seed_named(self, token: str) -> Seed | None:
        return next((s for s in self.seed_pool if s.opening_token == token), None)

    def _demo_projection(self) -> dict:
        demo = self.demo
        return {
            "opening": {"token": demo.opening.token, "emoji": demo.opening.emoji},
            "moves": [m.model_dump() for m in demo.moves],
            "headline": demo.headline,
            "points": [
                {
                    "name": r.name,
                    "earned": demo.scores[r.name] * r.weight,
                    "max_points": r.weight * SCORE_MAX,
                }
                for r in self.rubric
            ],
            "openings": [
                {
                    "token": o.token,
                    "emoji": self.seed_named(o.token).opening_emoji,  # type: ignore[union-attr]
                    "examples": o.examples,
                }
                for o in demo.openings
            ],
        }

    def player_projection(self) -> dict:
        """What the browser may see: no examples."""
        return {
            "slug": self.slug,
            "title": self.title,
            "tagline": self.tagline,
            "premise": self.premise.strip(),
            "rubric": [
                {"name": r.name, "description": r.description, "max_points": r.weight * SCORE_MAX}
                for r in self.rubric
            ],
            "rules_text": self.rules_text.strip(),
            "max_chars": self.move_constraints.max_chars,
            "move_prefix": self.move_constraints.prefix,
            "move_example": self.move_constraints.example,
            "move_hint": self.move_constraints.hint,
            "move_budget": self.move_budget,
            "score_max": SCORE_MAX,
            "host_name": self.host.persona_name,
            "demo": self._demo_projection(),
        }


def load_template(slug: str, version: int = 1) -> Template:
    path = TEMPLATES_DIR / slug / f"v{version}.yaml"
    return Template.model_validate(yaml.safe_load(path.read_text()))
