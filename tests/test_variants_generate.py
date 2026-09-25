import asyncio
import json
import random
from pathlib import Path

import pytest

from arena_core.template import load_template, load_template_file
from arena_evals.grow_seeds import append_to_pool
from arena_evals.variants import generate
from arena_evals.variants.generate import (
    Cell,
    Generator,
    build_template,
    dump_template,
    parent_shape,
    sample_cells,
)
from arena_evals.variants.spec import field_value, load_spec
from arena_judge.caller import CallResult

SPEC, PARENTS = load_spec("build")
DOMINO = PARENTS["domino"]
CELL = Cell(
    klass="build",
    parent="domino",
    theme="a rumour passing through a village",
    voice=SPEC.axes.voice[0],
    content_rating="mild",
    weights=[6, 3, 1],
)


def body_for(slug: str, premise: str) -> dict:
    body = json.loads(parent_shape(DOMINO, SPEC))
    body["slug"] = slug
    body["title"] = slug.title()
    body["premise"] = premise
    return body


def test_cells_are_reproducible_and_family_never_gets_a_savage_host():
    cells = sample_cells(SPEC, random.Random(0))
    assert cells == sample_cells(SPEC, random.Random(0))
    assert len(cells) == SPEC.cells
    assert len({c.theme for c in cells}) == SPEC.cells
    assert {c.parent for c in cells} == {"domino", "alibi"}
    for cell in cells:
        if cell.content_rating == "family":
            assert cell.voice.bite != "savage"
            assert cell.voice.explanation_adj not in ("snarky", "menacing")


def test_built_variant_keeps_the_mechanic_and_takes_the_cell_knobs(tmp_path: Path):
    template = build_template(SPEC, DOMINO, CELL, body_for("rumour", "A rumour grows."))
    for path in SPEC.fixed:
        assert field_value(template, path) == field_value(DOMINO, path)
    assert [r.weight for r in template.rubric] == [6, 3, 1]
    assert template.host.tone.commentary_adj == CELL.voice.commentary_adj
    assert template.host.bite == CELL.voice.bite
    assert template.demo.opening.token == template.seed_pool[0].opening_token
    path = tmp_path / "rumour.yaml"
    path.write_text(dump_template(template))
    again = load_template_file(path)
    assert again == template
    append_to_pool(again, [DOMINO.seed_pool[5]], path)
    assert len(load_template_file(path).seed_pool) == 4


def test_a_renamed_rubric_is_refused():
    body = body_for("rumour", "A rumour grows.")
    body["rubric"][0]["name"] = "spread"
    with pytest.raises(ValueError, match="rubric names"):
        build_template(SPEC, DOMINO, CELL, body)


class FakeLuna:
    """Answers sketches, full templates and rank scores by looking at what was asked."""

    def __init__(self):
        self.calls = 0

    async def complete(self, spec, messages, **extra) -> CallResult:
        self.calls += 1
        system, user = messages[0]["content"], messages[1]["content"]
        if system.startswith("You design"):
            sketches = [
                {
                    "slug": s,
                    "title": s,
                    "premise": "p",
                    "criterion": "c",
                    "move_shape": "m",
                    "first_card": "f",
                }
                for s in ("alpha", "beta", "gamma")
            ]
            return CallResult(text=json.dumps({"sketches": sketches}), latency_ms=1, cost_usd=0.01)
        if system.startswith("You write"):
            slug = user.split("The slug is ")[1].strip(".")
            premise = "Game beta." if slug == "gamma" else f"Game {slug}."
            body = body_for(slug, premise)
            text = json.dumps(body, ensure_ascii=False)
            return CallResult(text=text, latency_ms=1, cost_usd=0.02)
        score = 4 if "Game alpha." in user else 1
        return CallResult(
            text=json.dumps({"scores": [score] * len(SPEC.tests)}), latency_ms=1, cost_usd=0.001
        )

    async def aclose(self) -> None:
        return None


async def fake_embed(caller, texts, sem):
    def vec(text: str) -> list[float]:
        if "Game alpha." in text:
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0] if "Game beta." in text else [0.0, 0.0, 1.0]

    return [vec(t) for t in texts]


def point_pool_at(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(generate, "POOL_DIR", tmp_path / "pool")
    monkeypatch.setattr(generate, "RAW_DIR", tmp_path / "pool" / "raw")
    monkeypatch.setattr(generate, "INDEX_PATH", tmp_path / "pool" / "index.json")
    monkeypatch.setattr(generate, "embed_texts", fake_embed)


def test_one_cell_runs_through_lint_dedup_rank_and_split(monkeypatch, tmp_path: Path):
    point_pool_at(monkeypatch, tmp_path)
    luna = FakeLuna()
    gen = Generator(SPEC, PARENTS, luna)  # type: ignore[arg-type]
    linted = asyncio.run(gen.run_cell(CELL))
    assert sorted(linted) == ["alpha", "beta", "gamma"]
    assert luna.calls == 4
    asyncio.run(gen.dedup(0.99))
    gamma = gen.index["gamma"]
    assert gamma.stage == "dedup" and gamma.reject is not None and "beta" in gamma.reject
    assert not generate.pool_path("build", "gamma").exists()
    asyncio.run(gen.rank())
    alpha, beta = gen.index["alpha"], gen.index["beta"]
    assert alpha.stage == "rank" and alpha.reject is None and alpha.split == "train"
    assert beta.stage == "rank" and beta.reject is not None
    assert gen.tally.rejects == {"dup": 1, "rank": 1}
    generate.save_index(gen.index)
    again = Generator(SPEC, PARENTS, luna)  # type: ignore[arg-type]
    assert asyncio.run(again.run_cell(CELL)) == []
    assert luna.calls == 6
    assert round(gen.tally.spent, 3) == round(0.01 + 3 * 0.02 + 2 * 0.001, 3)


def test_a_tiny_budget_stops_after_the_probe_cell(monkeypatch, tmp_path: Path):
    point_pool_at(monkeypatch, tmp_path)
    luna = FakeLuna()
    monkeypatch.setattr(generate, "make_caller", lambda: luna)
    tally = asyncio.run(generate.generate("build", budget=0.001, threshold=0.99, seed=0))
    assert luna.calls == 4
    assert round(tally.spent, 3) == 0.07
    index = generate.load_index()
    assert {e.stage for e in index.values()} == {"lint"}


def test_promote_copies_a_pool_variant_and_demote_refuses_a_shipped_game(
    monkeypatch, tmp_path: Path
):
    point_pool_at(monkeypatch, tmp_path)
    monkeypatch.setattr(generate, "TEMPLATES_DIR", tmp_path / "templates")
    template = build_template(SPEC, DOMINO, CELL, body_for("rumour", "A rumour grows."))
    path = generate.pool_path("build", "rumour")
    path.parent.mkdir(parents=True)
    path.write_text(dump_template(template))
    generate.save_index(
        {"rumour": generate.Entry(klass="build", parent="domino", cell=CELL, stage="rank")}
    )
    dst = generate.promote("rumour")
    assert dst == tmp_path / "templates" / "rumour" / "v1.yaml"
    assert load_template_file(dst).slug == "rumour"
    generate.demote("rumour")
    assert not dst.exists()
    with pytest.raises(SystemExit, match="shipped"):
        generate.demote("then-i-am")
    assert load_template("then-i-am")
