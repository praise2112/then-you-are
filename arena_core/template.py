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


class MoveConstraints(Strict):
    max_chars: int = Field(gt=0)


class RubricEntry(Strict):
    name: str
    description: str
    weight: float = Field(gt=0, le=1)


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
    premise: str
    seed_pool: list[Seed] = Field(min_length=1)
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
        if abs(sum(r.weight for r in self.rubric) - 1.0) > 1e-6:
            raise ValueError("rubric weights must sum to 1")
        for ex in self.examples:
            if set(ex.scores) != set(names):
                raise ValueError(f"example scores {sorted(ex.scores)} do not match rubric {names}")
        for name, text in (
            ("judge_out_text", self.judge_out_text),
            ("nudge", self.validation_messages.nudge),
        ):
            if "{standing_form}" not in text:
                raise ValueError(f"{name} needs a {{standing_form}} slot")
        return self

    @property
    def weights(self) -> dict[str, float]:
        return {r.name: r.weight for r in self.rubric}

    def player_projection(self) -> dict:
        """What the browser may see: no examples, no weights."""
        return {
            "slug": self.slug,
            "title": self.title,
            "premise": self.premise.strip(),
            "rubric": [{"name": r.name, "description": r.description} for r in self.rubric],
            "rules_text": self.rules_text.strip(),
            "max_chars": self.move_constraints.max_chars,
            "move_budget": self.move_budget,
            "host_name": self.host.persona_name,
        }


def load_template(slug: str, version: int = 1) -> Template:
    path = TEMPLATES_DIR / slug / f"v{version}.yaml"
    return Template.model_validate(yaml.safe_load(path.read_text()))
