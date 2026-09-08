import type { ScoringPayload, TurnView } from "../api.ts";

const WEIGHTS: Record<string, number> = { counter_strength: 0.5, coherence: 0.3, novelty: 0.2 };

export const STANDING = new Set(["accept", "semantic_uncertain"]);

/** "I am the rust, patient, steel-eating." becomes "the rust". */
export function formName(move: string): string {
  const stripped = move.replace(/^\s*(then\s+)?i\s+am\s+/i, "").replace(/[.!?]+\s*$/, "");
  const head = stripped.split(/[,;:]/)[0].trim();
  return head || move;
}

export function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function truncate(text: string, max = 32): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

export function criterionLabel(name: string): string {
  return name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function total(scoring: ScoringPayload | null | undefined): number {
  if (!scoring) return 0;
  return Object.entries(scoring.scores).reduce(
    (sum, [name, value]) => sum + (WEIGHTS[name] ?? 0) * value,
    0,
  );
}

export function lastStanding(transcript: TurnView[]): TurnView | undefined {
  return [...transcript].reverse().find((t) => STANDING.has(t.outcome));
}

export function standingBefore(transcript: TurnView[], seq: number, seed: string): string {
  const before = transcript.filter((t) => t.seq < seq && STANDING.has(t.outcome));
  return before.length ? before[before.length - 1].move_text : seed;
}

export function rulingLine(turn: TurnView): string {
  if (turn.outcome === "fail") return "Broke against the standing form";
  if (turn.outcome === "semantic_uncertain") return "Close call, the move stands";
  const strength = turn.scoring?.scores.counter_strength;
  return strength === undefined ? "Accepted" : `Accepted, counter strength ${strength} of 4`;
}
