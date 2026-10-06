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

type Stamp = { won: boolean; two: boolean; winner: string };
type Card = { won: boolean; showcase: boolean; fellRound: number };
type Finish = { points: string; moves: number };

/** Each screen's words for a result. stamp: the result card, from the viewer's seat. card: a
 *  replay card, from the creator's seat. finish: the replay's final line, after the winner's name. */
export const RESULT_WORDS: Record<ResultKind, { stamp: (r: Stamp) => string; card: (r: Card) => string; finish: (r: Finish) => string }> = {
  draw: {
    stamp: () => "A draw",
    card: (r) => (r.showcase ? "A draw" : "Lost on points"),
    finish: (r) => `a draw, ${r.points}`,
  },
  points: {
    stamp: (r) => (r.won ? "Won on points" : r.two ? "Lost on points" : `${r.winner} won`),
    card: (r) => (r.won ? "Won on points" : "Lost on points"),
    finish: (r) => `wins on points, ${r.points}`,
  },
  sudden_death: {
    stamp: (r) => (r.won ? "Victory" : r.two ? "Defeat" : `${r.winner} won`),
    card: (r) => (r.won ? "Victory" : `Fell in round ${r.fellRound}`),
    finish: (r) => `wins by sudden death in ${r.moves} moves`,
  },
  resign: {
    stamp: (r) => (r.won ? "Victory" : "Resigned"),
    card: (r) => (r.won ? "The others resigned" : "Resigned"),
    finish: () => "wins by resignation",
  },
  forfeit: {
    stamp: (r) => (r.won ? "Victory" : "Out of turns"),
    card: (r) => (r.won ? "Last one standing" : "Out of turns"),
    finish: () => "wins as the others ran out of turns",
  },
  abandoned: {
    stamp: () => "A draw",
    card: (r) => `Fell in round ${r.fellRound}`,
    finish: (r) => `a draw, ${r.points}`,
  },
  unfilled: {
    stamp: () => "A draw",
    card: (r) => `Fell in round ${r.fellRound}`,
    finish: (r) => `a draw, ${r.points}`,
  },
};
