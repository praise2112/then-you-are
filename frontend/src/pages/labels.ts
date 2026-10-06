import type { Replay, TemplateView, TurnView } from "../api.ts";
import { formName } from "./format.ts";

export type ResultKind = Replay["result_kind"];

/** Where a verdict is read: a transcript line, a slip stamp, the standing move's stamp, a showcase entry. */
export type VerdictPlace = "line" | "stamp" | "standing" | "entry";

/** A judged turn's verdict in words. The standing stamp names a short form: "Point +6: a lantern". */
export function verdictLabel(turn: Pick<TurnView, "outcome" | "points" | "move_text">, place: VerdictPlace, prefix = ""): string {
  const points = turn.points ?? 0;
  const name = formName(turn.move_text, prefix);
  const named = (word: string) => (place !== "standing" || name.length > 28 ? word : `${word}: ${name}`);
  if (turn.outcome === "forfeit") return "Lost the turn";
  if (turn.outcome === "fail") return place === "entry" ? "Thrown out" : named("Fell");
  if (turn.outcome === "semantic_uncertain") return place === "line" ? `Close call, ${points}` : "Close call";
  return named(place === "line" ? `Point, ${points}` : `Point +${points}`);
}

export const BADGES: Record<string, { label: string; glyph: string; meaning: string }> = {
  close_call: { label: "Close call", glyph: "⚖", meaning: "A ruling too close to end a duel on." },
  accidental_truth: { label: "Accidental truth", glyph: "🎯", meaning: "Your bluff was the real meaning." },
  near_miss: { label: "Near miss", glyph: "◎", meaning: "A bluff a hair from the truth." },
};

/** The most a move can score: every rubric entry at full marks. */
export function totalPoints(template: TemplateView): number {
  return template.rubric.reduce((sum, r) => sum + r.max_points, 0);
}

/** Each rubric entry's points for a judged turn, and whether it is the criterion that decided the ruling. */
export function criterionMarks(turn: Pick<TurnView, "scoring" | "host">, template: TemplateView) {
  return template.rubric.map((entry) => ({
    entry,
    earned: (turn.scoring?.scores[entry.name] ?? 0) * (entry.max_points / template.score_max),
    decided: entry.name === turn.host?.because_clause.criterion,
  }));
}

type Words = { short: (won: boolean) => string; finish: (r: { points: string; moves: number }) => string };

/** One wording per result kind. short: from one player's seat, on the result card, a replay card or a
 *  profile row. finish: the replay's final line, after the winner's name. */
export const RESULT_WORDS: Record<ResultKind, Words> = {
  draw: {
    short: () => "A draw",
    finish: (r) => `a draw, ${r.points}`,
  },
  points: {
    short: (won) => (won ? "Won on points" : "Lost on points"),
    finish: (r) => `wins on points, ${r.points}`,
  },
  sudden_death: {
    short: (won) => (won ? "Victory" : "Defeat"),
    finish: (r) => `wins by sudden death in ${count(r.moves, "move")}`,
  },
  resign: {
    short: (won) => (won ? "Victory" : "Resigned"),
    finish: () => "wins by resignation",
  },
  forfeit: {
    short: (won) => (won ? "Victory" : "Out of turns"),
    finish: () => "wins as the others ran out of turns",
  },
  abandoned: {
    short: () => "Closed, no move for a day",
    finish: (r) => `closed, no move for a day, at ${r.points}`,
  },
  unfilled: {
    short: () => "Nobody joined",
    finish: () => "nobody joined",
  },
};

/** The result card's stamp: the short wording, except that a loser at a table of three or more
 *  is told who won a game decided by play. */
export function stampWords(kind: ResultKind, won: boolean, seats: number, winner: string): string {
  const named = !won && seats > 2 && (kind === "points" || kind === "sudden_death");
  return named ? `${winner} won` : RESULT_WORDS[kind].short(won);
}

/** How long a match ran: its rounds in showcase, its judged moves in escalation. */
export function lengthWords(showcase: boolean, judgedMoves: number, rounds: number): string {
  if (judgedMoves === 0) return "no moves";
  return showcase ? count(rounds, "round") : count(judgedMoves, "move");
}

function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}
