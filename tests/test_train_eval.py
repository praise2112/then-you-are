import asyncio
import json
import random
from pathlib import Path

import pytest

from arena_core.template import load_template
from arena_evals import train_eval
from arena_evals.common import load_model
from arena_evals.datagen.ledger import Ledger
from arena_evals.datagen.play import Teacher, new_match, play_match
from arena_evals.train_eval import (
    Context,
    Scored,
    length_matched,
    match_of,
    paired_difference,
    record_contexts,
    sample,
)
from arena_evals.variants.funnel import FLASH, JUDGE, deal_for
from tests.conftest import FakeCaller, judge_response
from tests.test_datagen_play import ScriptedCaller

DUEL = load_template("then-i-am")
MOVES = ["I am a hammer, rock-splitting.", "I am rust, hinge-eating.", "I am a poem."]


def played_contexts(tmp_path: Path) -> list[Context]:
    ledger = Ledger(tmp_path / "contexts.db")
    flash = Teacher(FLASH, load_model(FLASH))
    match = new_match(DUEL, deal_for(DUEL, "then-i-am", 0))
    match.id = "then-i-am-0"
    caller = ScriptedCaller(
        rulings=[judge_response(), judge_response(), judge_response(verdict="fail")],
        moves=list(MOVES),
    )
    asyncio.run(
        play_match(DUEL, match, {"p1": flash, "p2": flash}, caller, ledger, load_model(JUDGE))
    )
    replayer = FakeCaller([], [])
    contexts = asyncio.run(record_contexts([("counter", "then-i-am", DUEL)], 1, ledger, replayer))
    assert replayer.judged == []
    return contexts


def test_each_judged_flash_move_becomes_a_context_with_the_match_as_it_stood(tmp_path):
    contexts = played_contexts(tmp_path)
    assert [(c.seq, c.actor, c.flash.text) for c in contexts] == [
        (1, "p1", MOVES[0]),
        (2, "p2", MOVES[1]),
        (3, "p1", MOVES[2]),
    ]
    assert [c.flash.stood for c in contexts] == [True, True, False]
    assert [len(c.match["turns"]) for c in contexts] == [0, 1, 2]
    assert contexts[1].previous == MOVES[0]
    assert "YOUR MOVE" in contexts[0].messages[-1]["content"]
    rebuilt = match_of(contexts[2])
    assert [t.move_text for t in rebuilt.turns] == MOVES[:2]


@pytest.fixture
def eval_dir(tmp_path, monkeypatch):
    contexts = played_contexts(tmp_path)
    monkeypatch.setattr(train_eval, "CONTEXTS_PATH", tmp_path / "contexts.jsonl")
    monkeypatch.setattr(train_eval, "ROWS_DIR", tmp_path / "rows")
    monkeypatch.setattr(train_eval, "load_template_file", lambda _: DUEL)

    async def plenty(_):
        return 10.0

    monkeypatch.setattr(train_eval, "credit_left", plenty)
    (tmp_path / "contexts.jsonl").write_text("".join(c.model_dump_json() + "\n" for c in contexts))
    return contexts


def test_an_answer_the_rule_check_refuses_is_never_judged_and_a_rerun_replays(
    eval_dir, monkeypatch
):
    card = eval_dir[0].match["cards"][0]
    answers = [card, "I am a flood, hammer-rusting.", "I am a match, poem-burning."]
    rows = train_eval.ROWS_DIR
    rows.mkdir()
    (rows / "student.answers.jsonl").write_text(
        "".join(
            json.dumps({"context": c.id, "text": a}) + "\n"
            for c, a in zip(eval_dir, answers, strict=True)
        )
    )
    first = FakeCaller([judge_response(), judge_response(verdict="fail")], [])
    monkeypatch.setattr(train_eval, "make_caller", lambda **_: first)
    asyncio.run(train_eval.score("student", JUDGE, 1.0, None))
    assert first.judged == answers[1:]
    scored = train_eval.load_scored("student", JUDGE, eval_dir)
    assert [scored[c.id].outcome for c in eval_dir] == ["refused", "accept", "fail"]

    again = FakeCaller([], [])
    monkeypatch.setattr(train_eval, "make_caller", lambda **_: again)
    asyncio.run(train_eval.score("student", JUDGE, 1.0, None))
    assert again.judged == []
    assert train_eval.load_scored("student", JUDGE, eval_dir) == scored


def scored(stood: bool, text: str = "x", total: float = 0) -> Scored:
    return Scored(text=text, outcome="accept" if stood else "fail", total=total)


def test_the_stood_difference_is_paired_and_resamples_whole_matches():
    row = {f"m{m}/{i}": scored(i < 3) for m in range(4) for i in range(4)}
    base = {f"m{m}/{i}": scored(i < 2) for m in range(4) for i in range(4)}
    point, lo, hi = paired_difference(row, base, {k: k[:2] for k in row}, random.Random(0))
    assert point == pytest.approx(0.25)
    assert lo == pytest.approx(0.25) and hi == pytest.approx(0.25)

    uneven = {**row, **{f"m0/{i}": scored(True) for i in range(4)}}
    point, lo, hi = paired_difference(uneven, base, {k: k[:2] for k in row}, random.Random(0))
    assert lo < point < hi


def test_a_longer_answer_gains_nothing_from_length_alone():
    base = {str(n): scored(True, "x" * n, total=n / 10) for n in (10, 20, 30, 40)}
    row = {k: scored(True, "x" * 40, total=4.0) for k in base}
    assert length_matched(row, base) == pytest.approx(0)


def test_a_sample_is_the_same_every_run_and_keeps_each_class_share(tmp_path):
    base = played_contexts(tmp_path)[0]
    contexts = [
        base.model_copy(update={"id": f"{k}/{i}", "game_class": k})
        for k, n in (("counter", 60), ("showcase", 40))
        for i in range(n)
    ]
    picked = sample(contexts, 10)
    assert [c.id for c in picked] == [c.id for c in sample(contexts, 10)]
    assert sum(c.game_class == "counter" for c in picked) == 6
