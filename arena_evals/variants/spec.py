"""Class specs: fixed and varying template fields, sampling axes and sabotage expectations
for one game class, loaded from variants/classes/<class>.yaml and checked against the
class's example templates."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from arena_core.template import Strict, Template, load_template
from arena_evals.datagen.sabotage import kinds_for

CLASSES_DIR = Path(__file__).parent / "classes"

Expected = Literal["accept", "fail", "semantic_reject", "same_as_source"]


class Voice(Strict):
    commentary_adj: str
    explanation_adj: str
    bite: str
    ruling_generosity: str


class Axes(Strict):
    theme: list[str] = Field(min_length=20)
    voice: list[Voice] = Field(min_length=1)
    content_rating: dict[str, int]
    family_excludes: dict[str, list[str]]
    weights: list[list[int]] = Field(min_length=1)


class ClassSpec(Strict):
    name: str
    examples: list[str] = Field(min_length=1)
    cells: int = Field(gt=0)
    fixed: list[str] = Field(min_length=1)
    varies: list[str] = Field(min_length=1)
    axes: Axes
    sabotage_expectations: dict[str, Expected]
    tests: list[str] = Field(min_length=1)


def field_value(template: Template, path: str):
    """A template field by dotted path; raises AttributeError when the path does not exist."""
    value = template
    for part in path.split("."):
        value = getattr(value, part)
    return value


def check_against_examples(spec: ClassSpec, templates: dict[str, Template]) -> None:
    """Every fixed path resolves, every weight profile fits the rubric and keeps the leading
    criterion first, and the sabotage kinds are exactly those the class can produce."""
    for template in templates.values():
        for path in spec.fixed:
            field_value(template, path)
        for profile in spec.axes.weights:
            if len(profile) != len(template.rubric):
                raise ValueError(f"{spec.name}: profile {profile} does not fit {template.slug}")
            if profile != sorted(profile, reverse=True):
                raise ValueError(f"{spec.name}: profile {profile} is not descending")
        expected = set(spec.sabotage_expectations)
        if expected != set(kinds_for(template)):
            raise ValueError(f"{spec.name}: sabotage kinds {sorted(expected)} do not match")


def load_spec(name: str) -> tuple[ClassSpec, dict[str, Template]]:
    spec = ClassSpec.model_validate(yaml.safe_load((CLASSES_DIR / f"{name}.yaml").read_text()))
    templates = {slug: load_template(slug) for slug in spec.examples}
    check_against_examples(spec, templates)
    return spec, templates


def spec_names() -> list[str]:
    return sorted(p.stem for p in CLASSES_DIR.glob("*.yaml"))
