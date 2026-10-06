"""SQLite run ledger: one file per run, every model call written before the next starts."""

import dataclasses
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arena_core.template import Template
from arena_evals.common import judge_with_backoff, model_label, with_backoff
from arena_judge.caller import CallError, JudgeCall, ModelCaller, ModelSpec
from arena_judge.schema import JudgeResponse

SCHEMA = """
create table if not exists runs (
    run_id text primary key, plan text not null, started text default current_timestamp,
    finished text);
create table if not exists matches (
    match_id text primary key, template_id text not null, cards text not null,
    teacher_p1 text not null, teacher_p2 text not null, status text not null,
    outcome text);
create table if not exists calls (
    match_id text not null, idx integer not null, role text not null, actor text not null,
    seq integer not null, model text not null, prompt_hash text not null, raw text not null,
    reasoning text, payload text not null, tokens_in integer not null,
    tokens_out integer not null, cost_usd real not null, latency_ms integer not null,
    attempt text not null, primary key (match_id, idx));
create table if not exists sabotage (
    match_id text not null, seq integer not null, kind text not null, source text not null,
    mutated text not null, call_idx integer not null, expected text not null,
    outcome text not null, primary key (match_id, seq, kind));
create index if not exists calls_cost on calls (cost_usd);
"""


class CallFailed(Exception):
    """A model call failed upstream; nothing is recorded, so a resume asks again."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BudgetReached(Exception):
    """The budget ran out before a paid call; the match stays open for a resume."""


@dataclass(frozen=True)
class CallRow:
    match_id: str
    idx: int
    role: str
    actor: str
    seq: int
    model: str
    prompt_hash: str
    raw: str
    reasoning: str | None
    payload: dict[str, Any]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    attempt: str

    @property
    def verdict(self) -> JudgeResponse | None:
        """A judge row's parsed verdict, or None when the judge gave none."""
        response = self.payload["response"]
        return JudgeResponse.model_validate(response) if response else None


@dataclass(frozen=True)
class JudgeInputs:
    """What the judge rules on: the move to beat, the move, the hidden meaning, the transcript."""

    previous: str
    move: str
    hidden: str
    transcript: list[str]


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("pragma journal_mode=wal; pragma synchronous=normal;" + SCHEMA)
        row = self.conn.execute("select coalesce(sum(cost_usd), 0) as total from calls").fetchone()
        self._spent = float(row["total"])

    def close(self) -> None:
        self.conn.close()

    def create_run(self, run_id: str, plan: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "insert or ignore into runs (run_id, plan) values (?, ?)",
                (run_id, json.dumps(plan)),
            )

    def plan(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("select plan from runs where run_id = ?", (run_id,)).fetchone()
        return json.loads(row["plan"]) if row else None

    def finish_run(self, run_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "update runs set finished = current_timestamp where run_id = ?", (run_id,)
            )

    def add_match(
        self, match_id: str, template_id: str, cards: list[str], teacher_p1: str, teacher_p2: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                "insert or ignore into matches (match_id, template_id, cards, teacher_p1, "
                "teacher_p2, status) values (?, ?, ?, ?, ?, 'active')",
                (match_id, template_id, json.dumps(cards), teacher_p1, teacher_p2),
            )

    def set_match_status(self, match_id: str, status: str, outcome: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "update matches set status = ?, outcome = ? where match_id = ?",
                (status, json.dumps(outcome), match_id),
            )

    def ended_without_sabotage(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "select * from matches where status = 'ended' "
            "and json_extract(outcome, '$.sabotage') is null order by rowid"
        ).fetchall()

    def mark_sabotaged(self, match_id: str, count: int) -> None:
        with self.conn:
            self.conn.execute(
                "update matches set outcome = json_set(outcome, '$.sabotage', ?) "
                "where match_id = ?",
                (count, match_id),
            )

    def matches(self, status: str | None = None) -> list[sqlite3.Row]:
        if status is None:
            return self.conn.execute("select * from matches order by rowid").fetchall()
        return self.conn.execute(
            "select * from matches where status = ? order by rowid", (status,)
        ).fetchall()

    def add_call(self, row: CallRow) -> None:
        with self.conn:
            replaced = self.conn.execute(
                "select cost_usd from calls where match_id = ? and idx = ?", (row.match_id, row.idx)
            ).fetchone()
            self.conn.execute(
                "insert or replace into calls values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                dataclasses.astuple(dataclasses.replace(row, payload=json.dumps(row.payload))),
            )
        self._spent += row.cost_usd - (replaced["cost_usd"] if replaced else 0.0)

    def calls(self, match_id: str) -> list[CallRow]:
        rows = self.conn.execute(
            "select * from calls where match_id = ? order by idx", (match_id,)
        ).fetchall()
        return [_call_row(r) for r in rows]

    def all_calls(self) -> list[CallRow]:
        rows = self.conn.execute("select * from calls order by match_id, idx").fetchall()
        return [_call_row(r) for r in rows]

    def add_sabotage(
        self,
        match_id: str,
        seq: int,
        kind: str,
        source: str,
        mutated: str,
        call_idx: int,
        expected: str,
        outcome: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "insert or ignore into sabotage values (?, ?, ?, ?, ?, ?, ?, ?)",
                (match_id, seq, kind, source, mutated, call_idx, expected, outcome),
            )

    def sabotage_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute("select * from sabotage order by rowid").fetchall()

    def spent(self) -> float:
        """Dollars recorded in this ledger, summed once on opening and kept up to date since."""
        return self._spent


class Budget:
    """A spending limit on one ledger, counted from when the budget is made. A 402 from the
    provider, passed to `note_failure`, also stops it."""

    def __init__(self, ledger: Ledger, limit: float):
        self.ledger = ledger
        self.limit = limit
        self.start = ledger.spent()
        self.no_credit: CallFailed | None = None

    def spent(self) -> float:
        return self.ledger.spent() - self.start

    def over(self) -> bool:
        return self.spent() >= self.limit or self.no_credit is not None

    def note_failure(self, error: CallFailed) -> None:
        if error.status == 402 and self.no_credit is None:
            self.no_credit = error


def _call_row(r: sqlite3.Row) -> CallRow:
    return CallRow(**{**dict(r), "payload": json.loads(r["payload"])})


class Tape:
    """Record-or-replay cursor over one key's calls: a resumed run replays what it recorded
    and asks the model only for what comes after."""

    def __init__(self, ledger: Ledger, key: str, over_budget: Callable[[], bool] | None = None):
        self.ledger = ledger
        self.key = key
        self.over_budget = over_budget
        recorded = ledger.calls(key)
        self.unanswered: CallRow | None = None
        # A match that stopped on an unanswered verdict asks for that verdict again.
        if recorded and recorded[-1].role == "judge" and recorded[-1].payload["response"] is None:
            self.unanswered = recorded[-1]
            recorded = recorded[:-1]
        self.recorded = recorded
        self.idx = 0

    async def step(
        self,
        role: str,
        actor: str,
        seq: int,
        live: Callable[[int], Awaitable[CallRow]],
        inputs: dict[str, Any] | None = None,
    ) -> CallRow:
        """The next row, replayed when recorded. A recorded row that is not the call the
        caller is about to make, or was made on other `inputs`, means the run changed."""
        if self.idx < len(self.recorded):
            row = self.recorded[self.idx]
            if (row.role, row.actor, row.seq) != (role, actor, seq):
                raise RuntimeError(
                    f"{self.key}: replay diverged at call {self.idx}, recorded "
                    f"{row.role}/{row.actor}/{row.seq}, expected {role}/{actor}/{seq}"
                )
            if inputs is not None and {k: row.payload.get(k) for k in inputs} != inputs:
                raise RuntimeError(f"{self.key}: replay diverged at call {self.idx}, new inputs")
        else:
            if self.over_budget is not None and self.over_budget():
                raise BudgetReached(self.key)
            row = await live(self.idx)
            if self.unanswered is not None and self.unanswered.idx == row.idx:
                # The replaced row's paid attempts stay counted.
                row = dataclasses.replace(
                    row,
                    cost_usd=row.cost_usd + self.unanswered.cost_usd,
                    tokens_in=row.tokens_in + self.unanswered.tokens_in,
                    tokens_out=row.tokens_out + self.unanswered.tokens_out,
                )
            self.ledger.add_call(row)
        self.idx += 1
        return row

    async def judge(
        self,
        caller: ModelCaller,
        spec: ModelSpec,
        template: Template,
        actor: str,
        seq: int,
        ask: JudgeInputs,
    ) -> JudgeResponse:
        """The verdict on `ask`, replayed when recorded; raises CallFailed when there is none."""
        inputs = dataclasses.asdict(ask)

        async def live(idx: int) -> CallRow:
            call = await judge_with_backoff(
                caller, template, ask.transcript, ask.previous, ask.move, ask.hidden, spec
            )
            return judge_call_row(self.key, idx, actor, seq, spec, call, inputs)

        row = await self.step("judge", actor, seq, live, inputs)
        verdict = row.verdict
        if verdict is None:
            status = row.payload.get("error_status")
            raise CallFailed(f"{self.key}: judge gave no verdict ({status})", status)
        return verdict

    async def move(
        self,
        caller: ModelCaller,
        spec: ModelSpec,
        messages: list[dict],
        actor: str,
        seq: int,
        model: str,
        payload: dict[str, Any],
        inputs: dict[str, Any] | None = None,
    ) -> CallRow:
        """The writer's row for `messages`, replayed when recorded; raises CallFailed when the
        call fails for good. A recorded row must match `inputs`, all of `payload` by default."""

        async def live(idx: int) -> CallRow:
            try:
                result = await with_backoff(
                    lambda: caller.complete(spec, messages, reasoning={"enabled": False})
                )
            except CallError as e:
                raise CallFailed(
                    f"{self.key}: writer call failed ({e.status}): {e}", e.status
                ) from e
            return CallRow(
                self.key,
                idx,
                "move",
                actor,
                seq,
                model,
                "",
                result.text,
                result.reasoning,
                payload,
                result.tokens_in,
                result.tokens_out,
                result.cost_usd,
                result.latency_ms,
                "ok",
            )

        return await self.step("move", actor, seq, live, payload if inputs is None else inputs)


def judge_call_row(
    key: str, idx: int, actor: str, seq: int, spec: ModelSpec, call: JudgeCall, inputs: dict
) -> CallRow:
    return CallRow(
        match_id=key,
        idx=idx,
        role="judge",
        actor=actor,
        seq=seq,
        model=model_label(spec),
        prompt_hash=call.prompt_hash,
        raw=call.raw,
        reasoning=call.reasoning,
        payload={
            **inputs,
            "response": call.response.model_dump() if call.response else None,
            "attempts": call.attempts,
            "error_status": call.error_status,
        },
        tokens_in=call.tokens_in,
        tokens_out=call.tokens_out,
        cost_usd=call.cost_usd,
        latency_ms=call.latency_ms,
        attempt=call.attempts[-1] if call.attempts else "call_error",
    )
