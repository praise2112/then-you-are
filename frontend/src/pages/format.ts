import type { Replay, TurnView } from "../api.ts";

export const STANDING = new Set(["accept", "semantic_uncertain"]);

/** What the opponent is called during play. The model name shows only on replays. */
export const HOUSE = "The House";

/** Joins the template's fixed prefix to what the player typed, without doubling the prefix. */
export function fullMove(prefix: string, tail: string): string {
  const trimmed = tail.trim();
  const doubled = prefix && trimmed.toLowerCase().startsWith(prefix.trim().toLowerCase() + " ");
  return prefix + (doubled ? trimmed.slice(prefix.trim().length).trim() : trimmed);
}

/** The short name of a move: the prefix dropped, the first clause kept. */
export function formName(move: string, prefix = ""): string {
  const head0 = prefix && move.toLowerCase().startsWith(prefix.toLowerCase()) ? move.slice(prefix.length) : move;
  const stripped = head0.replace(/[.!?]+\s*$/, "");
  const head = stripped.split(/[,;:]/)[0].trim();
  return head || move;
}

export function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function criterionLabel(name: string): string {
  return name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function lastStanding(transcript: TurnView[]): TurnView | undefined {
  return [...transcript].reverse().find((t) => STANDING.has(t.outcome));
}

/** How a finished duel ended, from the player's side: "Victory", "Fell in round 3", "Won on points". */
export function resultLabel(replay: Replay): { text: string; won: boolean } {
  const won = replay.winner === "p1";
  if (replay.end_reason === "move_cap_points") return { text: won ? "Won on points" : "Lost on points", won };
  if (replay.end_reason === "resign") return { text: won ? "The House resigned" : "Resigned", won };
  if (won) return { text: "Victory", won };
  const fell = replay.transcript.filter((t) => t.actor === "p1").length;
  return { text: `Fell in round ${fell}`, won };
}
