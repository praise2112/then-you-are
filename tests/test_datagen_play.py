import asyncio
import random
from pathlib import Path

import pytest

from arena_core.state import Actor, Match, deal
from arena_core.template import load_template
from arena_evals.datagen.ledger import BudgetReached, CallFailed, Ledger, Tape
from arena_evals.datagen.play import Teacher, new_match, play_match
from arena_judge.caller import CallError, CallResult, ModelSpec
from tests.conftest import FakeCaller, judge_response

DUEL = load_template("then-i-am")
WORDS = load_template("word-for-word")
FLASH = Teacher("opponent-v1", ModelSpec(model="flash", display_name="Flash"))
LUNA = Teacher("opponent-luna", ModelSpec(model="luna", display_name="Luna"))
SIDES: dict[Actor, Teacher] = {"p1": FLASH, "p2": LUNA}
JUDGE = ModelSpec(model="flash-judge", display_name="The Judge")


class ScriptedCaller(FakeCaller):
    """Moves come from `complete`, verdicts from the fake judge. Records what each writer saw."""

    def __init__(self, rulings, moves, fail_after: int | None = None):
        super().__init__(rulings, [])
        self.moves = list(moves)
        self.writers_saw: list[tuple[str, list[dict]]] = []
        self.fail_after = fail_after
        self.completed = 0

    async def complete(self, spec, messages, **extra) -> CallResult:
        if self.fail_after is not None and self.completed >= self.fail_after:
            raise RuntimeError("crash")
        self.completed += 1
        self.writers_saw.append((spec.model, messages))
        assert extra["reasoning"] == {"enabled": False}
        return CallResult(text=self.moves.pop(0), latency_ms=1, cost_usd=0.001)


def duel_match() -> Match:
    return new_match(DUEL, [DUEL.seed_named("a rock")])  # type: ignore[list-item]


def word_match() -> Match:
    return new_match(WORDS, [WORDS.seed_named(t) for t in ("zarf", "groak", "ferrule")])  # type: ignore[list-item]


def test_deal_gives_one_card_to_a_duel_and_one_per_round_to_a_showcase():
    rng = random.Random(0)
    assert len(deal(DUEL, rng)) == 1
    cards = deal(WORDS, rng)
    assert len(cards) == WORDS.rounds_budget
    assert len({c.opening_token for c in cards}) == len(cards)


def test_a_duel_plays_to_sudden_death_and_every_call_lands_in_the_ledger(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    caller = ScriptedCaller(
        rulings=[judge_response(), judge_response(), judge_response(verdict="fail")],
        moves=["I am a hammer, rock-splitting.", '"I am rust, hinge-eating."', "I am a poem."],
    )
    match = asyncio.run(play_match(DUEL, duel_match(), SIDES, caller, ledger, JUDGE))
    assert match.status == "ended" and match.end_reason == "sudden_death"
    assert match.winner == "p2"
    assert [t.move_text for t in match.turns][1] == "I am rust, hinge-eating."
    assert [m for m, _ in caller.writers_saw] == ["flash", "luna", "flash"]
    rows = ledger.calls(match.id)
    assert [r.role for r in rows] == ["move", "judge"] * 3
    assert rows[1].payload["previous"] == "a rock" and rows[3].payload["previous"].startswith(
        "I am a hammer"
    )
    assert rows[0].payload["messages"] == caller.writers_saw[0][1]
    assert ledger.spent() == pytest.approx(0.003)
    assert ledger.matches("ended")[0]["teacher_p2"] == "opponent-luna"


def test_showcase_writers_see_only_finished_rounds_and_nobody_guesses(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    caller = ScriptedCaller(
        rulings=[judge_response()] * 6, moves=[f"a thing {i}" for i in range(6)]
    )
    match = asyncio.run(play_match(WORDS, word_match(), SIDES, caller, ledger, JUDGE))
    assert match.status == "ended" and match.end_reason == "rounds_complete"
    assert match.guesses == [] and match.phase == "write"
    assert [t.round_n for t in match.turns] == [1, 1, 2, 2, 3, 3]
    round_two_p2 = caller.writers_saw[3][1][1]["content"]
    assert "a thing 0" in round_two_p2 and "a thing 1" in round_two_p2
    assert "a thing 2" not in round_two_p2
    assert caller.hidden_seen[0] == WORDS.seed_named("zarf").hidden  # type: ignore[union-attr]


def test_a_truth_hit_is_rewritten_once_and_a_refusal_burns_a_strike(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    rulings = [
        judge_response(truth_proximity="hit"),
        judge_response(),
        judge_response(gates={"no_meta_move": False}),
        judge_response(),
    ] + [judge_response()] * 4
    caller = ScriptedCaller(rulings=rulings, moves=[f"a thing {i}" for i in range(9)])
    match = asyncio.run(play_match(WORDS, word_match(), SIDES, caller, ledger, JUDGE))
    assert [t.move_text for t in match.turns[:2]] == ["a thing 1", "a thing 3"]
    assert match.turns[0].truth_hit is False


def test_a_crashed_match_resumes_from_the_ledger_without_repeating_calls(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    match = duel_match()
    moves = ["I am a hammer, rock-splitting.", "I am rust, hinge-eating.", "I am a poem."]
    crashed = ScriptedCaller(rulings=[judge_response()] * 3, moves=list(moves), fail_after=2)
    with pytest.raises(RuntimeError):
        asyncio.run(play_match(DUEL, match, SIDES, crashed, ledger, JUDGE))
    assert len(ledger.calls(match.id)) == 4
    resumed = ScriptedCaller(rulings=[judge_response(verdict="fail")], moves=moves[2:])
    match = asyncio.run(
        play_match(DUEL, duel_match_with_id(match.id), SIDES, resumed, ledger, JUDGE)
    )
    assert match.status == "ended" and len(match.turns) == 3
    assert resumed.completed == 1 and len(ledger.calls(match.id)) == 6


def duel_match_with_id(match_id: str) -> Match:
    match = duel_match()
    match.id = match_id
    return match


def test_a_judge_that_stays_down_leaves_the_match_open_for_a_resume(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    caller = ScriptedCaller(rulings=[None] * 4, moves=["I am a hammer, rock-splitting."])
    with pytest.raises(CallFailed):
        asyncio.run(play_match(DUEL, duel_match(), SIDES, caller, ledger, JUDGE))
    row = ledger.matches("active")[0]
    assert ledger.calls(row["match_id"])[-1].payload["response"] is None


def test_an_unanswered_verdict_is_asked_again_on_resume_and_its_cost_is_kept(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    match = duel_match()
    down = ScriptedCaller(rulings=[None] * 4, moves=["I am a hammer, rock-splitting."])
    with pytest.raises(CallFailed):
        asyncio.run(play_match(DUEL, match, SIDES, down, ledger, JUDGE))
    spent = ledger.spent()
    back = ScriptedCaller(rulings=[judge_response(verdict="fail")], moves=[])
    match = asyncio.run(play_match(DUEL, duel_match_with_id(match.id), SIDES, back, ledger, JUDGE))
    assert match.status == "ended" and back.completed == 0
    assert [c.role for c in ledger.calls(match.id)] == ["move", "judge"]
    assert ledger.calls(match.id)[-1].payload["response"] is not None
    assert ledger.spent() >= spent


class NoCredit(ScriptedCaller):
    async def complete(self, spec, messages, **extra) -> CallResult:
        raise CallError("402 Payment Required", 402)


def test_a_failed_writer_call_records_nothing_and_says_why(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    match = duel_match()
    with pytest.raises(CallFailed) as failed:
        asyncio.run(play_match(DUEL, match, SIDES, NoCredit([], []), ledger, JUDGE))
    assert failed.value.status == 402
    assert ledger.calls(match.id) == [] and ledger.matches("active")


def test_a_spent_budget_stops_before_the_next_paid_call(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    caller = ScriptedCaller(rulings=[judge_response()], moves=["I am a hammer, rock-splitting."])
    with pytest.raises(BudgetReached):
        asyncio.run(play_match(DUEL, duel_match(), SIDES, caller, ledger, JUDGE, lambda: True))
    assert caller.completed == 0 and ledger.all_calls() == []


def test_a_replayed_call_made_on_other_inputs_stops_the_run(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    caller = ScriptedCaller(
        rulings=[judge_response(verdict="fail")], moves=["I am a hammer, rock-splitting."]
    )
    match = duel_match()
    asyncio.run(play_match(DUEL, match, SIDES, caller, ledger, JUDGE))
    tape = Tape(ledger, match.id)

    async def never(idx: int):
        raise AssertionError("must replay")

    with pytest.raises(RuntimeError, match="new inputs"):
        asyncio.run(tape.step("move", "p1", 1, never, {"messages": []}))


def test_a_replayed_row_that_is_not_the_expected_call_stops_the_run(tmp_path: Path):
    ledger = Ledger(tmp_path / "run.db")
    caller = ScriptedCaller(
        rulings=[judge_response(verdict="fail")], moves=["I am a hammer, rock-splitting."]
    )
    match = duel_match()
    asyncio.run(play_match(DUEL, match, SIDES, caller, ledger, JUDGE))
    tape = Tape(ledger, match.id)

    async def never(idx: int):
        raise AssertionError("must replay")

    asyncio.run(tape.step("move", "p1", 1, never))
    with pytest.raises(RuntimeError, match="replay diverged"):
        asyncio.run(tape.step("move", "p2", 2, never))
