"""One synthetic match through the engine's own code, every model call recorded in the ledger.

A match resumes by replaying its recorded calls in order, so a crash costs nothing."""

import secrets
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
from arena_evals.common import judge_with_backoff
from arena_evals.datagen.ledger import CallRow, Ledger, Tape, judge_call_row, move_call_row
from arena_judge.caller import ModelCaller, ModelSpec
from arena_judge.prompt import clean_move, judge_prompt_hash, render_opponent_messages
from arena_judge.schema import JudgeResponse, Outcome, route_outcome


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
        self.judge_spec = judge
        self.tape = Tape(ledger, match.id)

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

    async def _write(self, actor: Actor, prompt: str, hidden: str) -> str:
        match, template = self.match, self.template
        teacher = self.teachers[actor]
        lines = transcript(match, template, finished_only=True)
        messages = render_opponent_messages(template, prompt, lines, hidden)
        seq = len(match.turns) + 1
        payload = {"card": prompt, "transcript": lines, "hidden": hidden, "messages": messages}
        row = await self.tape.step(
            "move",
            actor,
            seq,
            lambda idx: move_call_row(
                self.caller, teacher.spec, messages, match.id, idx, actor, seq, teacher.ref, payload
            ),
        )
        return clean_move(row.raw)

    async def _judge(
        self, actor: Actor, previous: str, move: str, hidden: str, lines: list[str]
    ) -> Judged:
        match = self.match
        seq = len(match.turns) + 1
        inputs = {"previous": previous, "move": move, "hidden": hidden, "transcript": lines}

        async def live(idx: int) -> CallRow:
            call = await judge_with_backoff(
                self.caller, self.template, lines, previous, move, hidden, self.judge_spec
            )
            return judge_call_row(match.id, idx, actor, seq, self.judge_spec, call, inputs)

        row = await self.tape.step("judge", actor, seq, live)
        if row.payload["response"] is None:
            raise MatchAbandoned(f"{match.id}: judge unavailable after retries")
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
