"""One synthetic match through the engine's own code, every model call recorded in the ledger.

A match resumes by replaying its recorded calls in order, so a crash costs nothing."""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from arena_core.state import (
    PLAYERS,
    Actor,
    Match,
    apply_ruling,
    layer1,
    resign,
    transcript,
    weighted_total,
)
from arena_core.template import Seed, Template
from arena_evals.common import model_label, with_backoff
from arena_evals.datagen.ledger import CallRow, Ledger
from arena_judge.caller import CallError, JudgeCall, ModelCaller, ModelSpec
from arena_judge.prompt import judge_prompt_hash, render_opponent_messages
from arena_judge.schema import JudgeResponse, Outcome, route_outcome

JUDGE_RETRIES = 3
JUDGE_RETRY_S = 5.0


class MatchAbandoned(Exception):
    """The match cannot go on: the judge stayed down, or no legal move was left to play."""


@dataclass(frozen=True)
class Teacher:
    ref: str
    spec: ModelSpec


@dataclass(frozen=True)
class Judged:
    outcome: Outcome
    response: JudgeResponse
    text: str


def deal(template: Template) -> list[Seed]:
    """One card for an escalation duel, one per round for a showcase."""
    if template.mode == "escalation":
        return [secrets.choice(template.seed_pool)]
    return secrets.SystemRandom().sample(template.seed_pool, template.move_budget // 2)


def new_match(template: Template, cards: list[Seed]) -> Match:
    return Match(
        id=secrets.token_urlsafe(8),
        template_id=template.slug,
        template_version=template.schema_version,
        cards=[c.opening_token for c in cards],
        seed_emoji=cards[0].opening_emoji,
        guessers=(),
    )


class MatchPlayer:
    def __init__(
        self,
        template: Template,
        match: Match,
        teachers: dict[Actor, Teacher],
        caller: ModelCaller,
        ledger: Ledger,
        judge: ModelSpec,
    ):
        self.template = template
        self.match = match
        self.teachers = teachers
        self.caller = caller
        self.ledger = ledger
        self.judge_spec = judge
        self.recorded = ledger.calls(match.id)
        self.idx = 0

    async def play(self) -> Match:
        if self.template.mode == "escalation":
            await self._play_escalation()
        else:
            await self._play_showcase()
        return self.match

    # Escalation

    async def _play_escalation(self) -> None:
        match, template = self.match, self.template
        while match.status == "active":
            actor = match.to_move
            strikes_before = match.strikes[actor]
            while match.status == "active" and match.to_move == actor:
                refusals = match.strikes[actor] - strikes_before
                if refusals < template.strikes_before_consequence:
                    text = await self._write(actor, match.seed, "")
                elif refusals == template.strikes_before_consequence:
                    text = template.default_move
                else:
                    resign(match, actor, match.state_version)
                    break
                await self._play_move(actor, text)

    async def _play_move(self, actor: Actor, text: str) -> None:
        match, template = self.match, self.template
        if layer1(template, text, match) is not None:
            apply_ruling(match, actor, text, "deterministic_invalid", match.state_version, template)
            return
        judged = await self._judge(
            actor, match.standing_form, text, "", transcript(match, template)
        )
        points = weighted_total(judged.response.scoring.scores, template.weights)
        apply_ruling(match, actor, text, judged.outcome, match.state_version, template, points)

    # Showcase

    async def _play_showcase(self) -> None:
        match, template = self.match, self.template
        while match.status == "active":
            card = template.seed_named(match.card)
            assert card is not None
            lines = transcript(match, template)
            for actor in PLAYERS:
                judged = await self._write_and_judge_bluff(actor, card, lines)
                proximity = judged.response.scoring.truth_proximity if card.hidden else "none"
                points = weighted_total(judged.response.scoring.scores, template.weights)
                apply_ruling(
                    match,
                    actor,
                    judged.text,
                    judged.outcome,
                    match.state_version,
                    template,
                    points,
                    truth_hit=proximity == "hit",
                )

    async def _write_and_judge_bluff(self, actor: Actor, card: Seed, lines: list[str]) -> Judged:
        """Rewrites once on a truth hit, and on each refusal until the strikes run out."""
        template = self.template
        text = await self._write(actor, card.card_text, card.hidden)
        refusals = 0
        retold = False
        while True:
            if layer1(template, text, self.match) is None:
                judged = await self._judge(actor, card.card_text, text, card.hidden, lines)
                hit = judged.response.scoring.truth_proximity == "hit"
                if hit and card.hidden and not retold:
                    retold = True
                    text = await self._write(actor, card.card_text, card.hidden)
                    continue
                if judged.outcome != "semantic_reject":
                    return judged
            refusals += 1
            if refusals < template.strikes_before_consequence:
                text = await self._write(actor, card.card_text, card.hidden)
            elif text == template.default_move:
                raise MatchAbandoned(f"{self.match.id}: the default move was refused")
            else:
                text = template.default_move

    # Calls, recorded or replayed

    async def _record(self, live: Callable[[], Awaitable[CallRow]]) -> CallRow:
        if self.idx < len(self.recorded):
            row = self.recorded[self.idx]
        else:
            row = await live()
            self.ledger.add_call(row)
        self.idx += 1
        return row

    async def _write(self, actor: Actor, prompt: str, hidden: str) -> str:
        match, template = self.match, self.template
        teacher = self.teachers[actor]
        lines = transcript(match, template, finished_only=True)
        messages = render_opponent_messages(template, prompt, lines, hidden)
        seq = len(match.turns) + 1
        payload = {"card": prompt, "transcript": lines, "hidden": hidden, "messages": messages}

        async def live() -> CallRow:
            base = dict(
                match_id=match.id,
                idx=self.idx,
                role="move",
                actor=actor,
                seq=seq,
                model=teacher.ref,
                prompt_hash="",
                payload=payload,
            )
            try:
                result = await with_backoff(
                    lambda: self.caller.complete(
                        teacher.spec, messages, reasoning={"enabled": False}
                    )
                )
            except CallError as e:
                return CallRow(
                    raw="",
                    reasoning=None,
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0.0,
                    latency_ms=0,
                    attempt=f"call_error: {e}",
                    **base,  # type: ignore[arg-type]
                )
            return CallRow(
                raw=result.text,
                reasoning=result.reasoning,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
                attempt="ok",
                **base,  # type: ignore[arg-type]
            )

        row = await self._record(live)
        return row.raw.strip().strip('"')

    async def _judge_with_retries(
        self, lines: list[str], previous: str, move: str, hidden: str
    ) -> JudgeCall:
        template = self.template
        call = await self.caller.judge(
            template, lines, previous, move, hidden, spec=self.judge_spec
        )
        for _ in range(JUDGE_RETRIES):
            if call.response is not None:
                break
            await asyncio.sleep(JUDGE_RETRY_S)
            call = await self.caller.judge(
                template, lines, previous, move, hidden, spec=self.judge_spec
            )
        return call

    async def _judge(
        self, actor: Actor, previous: str, move: str, hidden: str, lines: list[str]
    ) -> Judged:
        match = self.match
        seq = len(match.turns) + 1
        inputs = {"previous": previous, "move": move, "hidden": hidden, "transcript": lines}

        async def live() -> CallRow:
            call = await self._judge_with_retries(lines, previous, move, hidden)
            response = call.response.model_dump() if call.response else None
            return CallRow(
                match_id=match.id,
                idx=self.idx,
                role="judge",
                actor=actor,
                seq=seq,
                model=model_label(self.judge_spec),
                prompt_hash=call.prompt_hash,
                raw=call.raw,
                reasoning=call.reasoning,
                payload={**inputs, "response": response, "attempts": call.attempts},
                tokens_in=call.tokens_in,
                tokens_out=call.tokens_out,
                cost_usd=call.cost_usd,
                latency_ms=call.latency_ms,
                attempt=call.attempts[-1] if call.attempts else "call_error",
            )

        row = await self._record(live)
        if row.payload["response"] is None:
            raise MatchAbandoned(f"{match.id}: judge unavailable after {JUDGE_RETRIES} retries")
        response = JudgeResponse.model_validate(row.payload["response"])
        return Judged(route_outcome(response.scoring), response, move)


async def play_match(
    template: Template,
    match: Match,
    teachers: dict[Actor, Teacher],
    caller: ModelCaller,
    ledger: Ledger,
    judge: ModelSpec,
) -> Match:
    """Plays or resumes one match and records its final status in the ledger."""
    ledger.add_match(match.id, template.slug, match.cards, teachers["p1"].ref, teachers["p2"].ref)
    player = MatchPlayer(template, match, teachers, caller, ledger, judge)
    try:
        await player.play()
    except MatchAbandoned as e:
        ledger.set_match_status(match.id, "abandoned", {"reason": str(e)})
        raise
    ledger.set_match_status(
        match.id,
        "ended",
        {
            "winner": match.winner,
            "end_reason": match.end_reason,
            "points": match.points,
            "turns": len(match.turns),
            "judge_prompt_hash": judge_prompt_hash(template),
        },
    )
    return match
