"""Corpus records derived from a run ledger: player, judge and host files plus the drop counts."""

import json
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from arena_core.state import STANDING, normalize, weighted_total
from arena_core.template import Template
from arena_evals.datagen.ledger import CallRow, Ledger
from arena_evals.run_golden import GOLDEN_DIR, load_golden
from arena_judge.prompt import (
    JudgedTurn,
    clean_move,
    render_judge_messages,
    render_judge_prompt,
    render_opponent_messages,
)
from arena_judge.schema import JudgeResponse, Outcome, route_outcome

PARSED = ("parsed", "parsed_on_retry")
Quantile = Literal["low", "mid", "high"]


class Provenance(BaseModel):
    template_id: str
    template_version: int
    game_class: str
    split: str
    teacher: str
    judge_model: str
    judge_prompt_hash: str
    outcome: Outcome
    scores: dict[str, int]
    weighted_score: int
    score_quantile: Quantile | None
    confidence: str
    match_id: str
    seq: int
    card: str
    transcript: list[str]
    hidden: str
    target_quality: str


class PlayerRecord(BaseModel):
    messages: list[dict[str, str]]
    target: str
    provenance: Provenance


class JudgeRecord(BaseModel):
    template_id: str
    judge_model: str
    judge_prompt_hash: str
    prompt: str
    messages: list[dict[str, str]]
    raw: str
    reasoning: str | None
    response: dict[str, Any] | None
    outcome: Outcome | None
    attempt: str
    target_quality: str
    match_id: str
    seq: int


class HostRecord(BaseModel):
    template_id: str
    input: dict[str, Any]
    headline: str
    because_clause: dict[str, str]
    match_id: str
    seq: int


class Export(BaseModel):
    player: int = 0
    judge: int = 0
    host: int = 0
    disputed: int = 0
    drops: dict[str, int] = {}


class RenderMismatch(Exception):
    """A stored writer's system prompt no longer matches what the serving code renders."""


def golden_pairs() -> set[tuple[str, str]]:
    pairs = set()
    for folder in GOLDEN_DIR.iterdir():
        for split in ("dev", "holdout"):
            for r in load_golden(folder.name, split):  # type: ignore[arg-type]
                pairs.add((normalize(r.previous_move), normalize(r.move)))
    return pairs


def terciles(scores: Iterable[int]) -> tuple[float, float] | None:
    values = sorted(scores)
    if len(values) < 3:
        return None
    cuts = statistics.quantiles(values, n=3)
    return cuts[0], cuts[1]


def quantile_of(score: int, cuts: tuple[float, float] | None) -> Quantile | None:
    if cuts is None:
        return None
    return "low" if score <= cuts[0] else "mid" if score <= cuts[1] else "high"


def export(
    ledger: Ledger,
    templates: dict[str, Template],
    classes: dict[str, str],
    out_dir: Path,
    split: str = "train",
    sabotage_teacher: str | None = None,
) -> Export:
    """Writes player.jsonl, judge.jsonl, host.jsonl and disputed.jsonl under out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    contaminated = golden_pairs()
    matches = {row["match_id"]: row for row in ledger.matches()}
    calls_by_match = {mid: ledger.calls(mid) for mid in matches}
    sabotage_by_call = {
        (row["match_id"] + "/sabotage", row["call_idx"]): row for row in ledger.sabotage_rows()
    }
    for key in {k[0] for k in sabotage_by_call}:
        calls_by_match[key] = ledger.calls(key)

    drops: Counter[str] = Counter()
    players: list[PlayerRecord] = []
    judges: list[JudgeRecord] = []
    hosts: list[HostRecord] = []
    disputed: list[JudgeRecord] = []

    for match_key, calls in calls_by_match.items():
        match_id = match_key.removesuffix("/sabotage")
        match = matches[match_id]
        template = templates[match["template_id"]]
        teachers = {"p1": match["teacher_p1"], "p2": match["teacher_p2"]}
        last_move: CallRow | None = None
        for call in calls:
            if call.role == "move":
                last_move = call
                continue
            sabotage = sabotage_by_call.get((match_key, call.idx))
            quality = sabotage["kind"] if sabotage else "best"
            response = (
                JudgeResponse.model_validate(call.payload["response"])
                if call.payload["response"]
                else None
            )
            outcome = route_outcome(response.scoring) if response else None
            before = (call.seq, -1 if sabotage else call.idx)
            earlier = judged_before(calls_by_match.get(match_id, []), before)
            record = JudgeRecord(
                template_id=template.slug,
                judge_model=call.model,
                judge_prompt_hash=call.prompt_hash,
                prompt=render_judge_prompt(
                    template,
                    call.payload["transcript"],
                    call.payload["previous"],
                    call.payload["move"],
                    call.payload["hidden"],
                ),
                messages=render_judge_messages(
                    template,
                    earlier,
                    call.actor,
                    call.payload["previous"],
                    call.payload["move"],
                    call.payload["hidden"],
                ),
                raw=call.raw,
                reasoning=call.reasoning,
                response=call.payload["response"],
                outcome=outcome,
                attempt=call.attempt,
                target_quality=quality,
                match_id=match_id,
                seq=call.seq,
            )
            if sabotage and outcome != sabotage["expected"]:
                disputed.append(record)
                continue
            judges.append(record)
            if response is None or outcome is None:
                continue
            if call.attempt in PARSED:
                _admit_host(hosts, drops, template, call, response, outcome, match_id)
            if sabotage and quality != "weak_but_legal":
                continue
            if last_move is None or clean_move(last_move.raw) != call.payload["move"]:
                drops["player: no student move"] += 1
                continue
            reason = _player_drop_reason(template, call, response, outcome, contaminated)
            if reason:
                drops[f"player: {reason}"] += 1
                continue
            players.append(
                _player_record(
                    template,
                    classes,
                    split,
                    # A weak_but_legal move was written by the saboteur, not the seat's teacher.
                    (sabotage_teacher if sabotage else None) or teachers[call.actor],
                    last_move,
                    call,
                    response,
                    outcome,
                    quality,
                )
            )

    _stamp_quantiles(players)
    _write(out_dir / "player.jsonl", players)
    _write(out_dir / "judge.jsonl", judges)
    _write(out_dir / "host.jsonl", hosts)
    _write(out_dir / "disputed.jsonl", disputed)
    return Export(
        player=len(players),
        judge=len(judges),
        host=len(hosts),
        disputed=len(disputed),
        drops=dict(drops),
    )


def judged_before(calls: list[CallRow], before: tuple[int, int]) -> list[JudgedTurn]:
    """The match's judged moves ahead of `before` (seq, idx), each with the verdict it got."""
    return [
        JudgedTurn(
            c.actor,
            c.payload["previous"],
            c.payload["move"],
            c.payload["hidden"],
            json.dumps(c.payload["response"], ensure_ascii=False),
        )
        for c in calls
        if c.role == "judge" and c.payload["response"] and (c.seq, c.idx) < before
    ]


def _player_drop_reason(
    template: Template,
    call: CallRow,
    response: JudgeResponse,
    outcome: Outcome,
    contaminated: set[tuple[str, str]],
) -> str | None:
    move = call.payload["move"]
    if outcome not in STANDING:
        return "not accepted"
    if call.attempt not in PARSED:
        return "salvaged verdict"
    if len(move) > template.move_constraints.max_chars:
        return "over max_chars"
    if call.payload["hidden"] and response.scoring.truth_proximity == "hit":
        return "truth hit"
    if (normalize(call.payload["previous"]), normalize(move)) in contaminated:
        return "golden contamination"
    return None


def _player_record(
    template: Template,
    classes: dict[str, str],
    split: str,
    teacher: str,
    move_call: CallRow,
    call: CallRow,
    response: JudgeResponse,
    outcome: Outcome,
    quality: str,
) -> PlayerRecord:
    inputs = move_call.payload
    rendered = render_opponent_messages(
        template, inputs["card"], inputs["transcript"], inputs["hidden"], seat=move_call.actor
    )
    if rendered[0] != inputs["messages"][0]:
        raise RenderMismatch(f"{call.match_id} seq {call.seq}")
    return PlayerRecord(
        messages=rendered,
        target=call.payload["move"],
        provenance=Provenance(
            template_id=template.slug,
            template_version=template.schema_version,
            game_class=classes[template.slug],
            split=split,
            teacher=teacher,
            judge_model=call.model,
            judge_prompt_hash=call.prompt_hash,
            outcome=outcome,
            scores=response.scoring.scores,
            weighted_score=weighted_total(response.scoring.scores, template.weights),
            score_quantile=None,
            confidence=response.scoring.confidence,
            match_id=call.match_id.removesuffix("/sabotage"),
            seq=call.seq,
            card=inputs["card"],
            transcript=inputs["transcript"],
            hidden=inputs["hidden"],
            target_quality=quality,
        ),
    )


def _admit_host(
    hosts: list[HostRecord],
    drops: Counter[str],
    template: Template,
    call: CallRow,
    response: JudgeResponse,
    outcome: Outcome,
    match_id: str,
) -> None:
    if response.scoring.verdict == "fail" and outcome == "semantic_uncertain":
        drops["host: fail narrated on a move that stood"] += 1
        return
    hosts.append(
        HostRecord(
            template_id=template.slug,
            input={
                "scoring": response.scoring.model_dump(),
                "host": template.host.model_dump(),
                "previous": call.payload["previous"],
                "move": call.payload["move"],
                "transcript": call.payload["transcript"],
                "outcome": outcome,
            },
            headline=response.host.headline,
            because_clause=response.host.because_clause.model_dump(),
            match_id=match_id,
            seq=call.seq,
        )
    )


def _stamp_quantiles(players: list[PlayerRecord]) -> None:
    """Terciles of weighted score per template and judge version, over the admitted moves."""
    groups: dict[tuple[str, str, str], list[int]] = {}
    for rec in players:
        p = rec.provenance
        groups.setdefault((p.template_id, p.judge_model, p.judge_prompt_hash), []).append(
            p.weighted_score
        )
    cuts = {key: terciles(values) for key, values in groups.items()}
    for rec in players:
        p = rec.provenance
        p.score_quantile = quantile_of(
            p.weighted_score, cuts[(p.template_id, p.judge_model, p.judge_prompt_hash)]
        )


def _write(path: Path, records: Sequence[BaseModel]) -> None:
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec.model_dump(), ensure_ascii=False) + "\n")
