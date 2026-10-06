import { useEffect, useState } from "react";

import type { MatchEnded, MatchSnapshot, Replay, RoundView, SeatView, TemplateView, TurnView } from "../api.ts";

export const STANDING = new Set(["accept", "semantic_uncertain"]);

/** What the opponent is called during play. The model name shows only on replays. */
export const HOUSE = "The House";

/** Joins the template's fixed prefix to what the player typed, without doubling the prefix. */
export function fullMove(prefix: string, tail: string): string {
  const trimmed = tail.trim();
  const doubled = prefix && trimmed.toLowerCase().startsWith(prefix.trim().toLowerCase() + " ");
  return prefix + (doubled ? trimmed.slice(prefix.trim().length).trim() : trimmed);
}

/** A move without the template's fixed prefix, when it starts with it. */
export function withoutPrefix(move: string, prefix: string): string {
  return prefix && move.toLowerCase().startsWith(prefix.toLowerCase()) ? move.slice(prefix.length) : move;
}

/** The short name of a move: the prefix dropped, the first clause kept. */
export function formName(move: string, prefix = ""): string {
  const stripped = withoutPrefix(move, prefix).replace(/[.!?]+\s*$/, "");
  const head = stripped.split(/[,;:]/)[0].trim();
  return head || move;
}

const FACES = {
  clear: ["ヽ(•‿•)ノ", "(ﾉ◕ヮ◕)ﾉ", "ᕦ(ò_óˇ)ᕤ", "(•̀ᴗ•́)و"],
  narrow: ["( ˘ω˘ )", "(￣ー￣)", "(¬‿¬)"],
  close: ["(・_・;)", "(⊙_⊙;)", "ಠ_ಠ"],
  fall: ["(╯°□°)╯", "(￣▽￣;)", "(－_－;)"],
};

/** The judge's face for a judged move: the mood from the verdict, the face from the move number,
 *  so a replay shows the same face every time. */
export function judgeFace(turn: Pick<TurnView, "seq" | "outcome" | "scoring">): { face: string; fall: boolean } {
  const mood =
    turn.outcome === "fail"
      ? "fall"
      : turn.outcome === "semantic_uncertain"
        ? "close"
        : turn.scoring?.confidence === "clear"
          ? "clear"
          : "narrow";
  const faces = FACES[mood];
  return { face: faces[turn.seq % faces.length], fall: mood === "fall" };
}

/** A card longer than a label line gets its own block in body type. */
export function isLongCard(text: string): boolean {
  return text.length > 60;
}

/** A card or move short enough to sit inside a label; longer text is cut at a word, with an ellipsis. */
export function shortName(text: string, max = 28): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 0 ? cut.lastIndexOf(" ") : max).trimEnd()}…`;
}

export function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A rubric entry's own label when the template gives one, else the name made readable. */
export function criterionLabel(name: string, template?: TemplateView): string {
  const label = template?.rubric.find((r) => r.name === name)?.label;
  return label ?? name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function lastStanding(transcript: TurnView[]): TurnView | undefined {
  return [...transcript].reverse().find((t) => STANDING.has(t.outcome));
}

export function templateOf(templates: TemplateView[] | null, slug: string): TemplateView | undefined {
  return templates?.find((t) => t.slug === slug);
}

/** Who sits where. The viewer's seat reads "You" and is always red; the other seats take the
 *  remaining colours in turn order. A visitor sees the table from its first human seat. */
export type Table = {
  me: string | null;
  seat: (seat: string) => SeatView | undefined;
  name: (seat: string) => string;
  tone: (seat: string) => number;
};

export function tableOf(snap: Pick<MatchSnapshot, "seats" | "your_seat">, spectator = false): Table {
  const me = spectator ? null : snap.your_seat;
  const anchor = me ?? snap.seats.find((s) => s.kind === "human")?.seat ?? "p1";
  const order = [anchor, ...snap.seats.map((s) => s.seat).filter((s) => s !== anchor)];
  const byId = new Map(snap.seats.map((s) => [s.seat, s]));
  return {
    me,
    seat: (seat) => byId.get(seat),
    name: (seat) => (seat === me ? "You" : (byId.get(seat)?.display_name ?? seat)),
    tone: (seat) => order.indexOf(seat) + 1,
  };
}

/** A match still being played, whatever the judge is doing. */
export function isLive(status: MatchSnapshot["status"]): boolean {
  return status === "active" || status === "awaiting_judgment" || status === "paused";
}

/** How a finished game ended, seen from its creator's seat: "Victory", "Fell in round 3". */
export function resultLabel(replay: Replay): { text: string; won: boolean } {
  const owner = replay.seats.find((s) => s.kind === "human")?.seat ?? "p1";
  const won = replay.winner === owner;
  if (replay.winner === null && replay.end_reason === "rounds_complete") return { text: "A draw", won };
  if (replay.end_reason === "move_cap_points" || replay.end_reason === "rounds_complete") {
    return { text: won ? "Won on points" : "Lost on points", won };
  }
  if (replay.end_reason === "resign") return { text: won ? "The others resigned" : "Resigned", won };
  if (replay.end_reason === "forfeit") return { text: won ? "Last one standing" : "Out of turns", won };
  if (won) return { text: "Victory", won };
  const fell = replay.transcript.filter((t) => t.actor === owner).length;
  return { text: `Fell in round ${fell}`, won };
}

export type RoundGroup = { round: RoundView; turns: TurnView[]; revealed: boolean };

/** Each dealt card with its judged answers in seat order. A round is revealed once its truth is known. */
export function groupRounds(rounds: RoundView[], transcript: TurnView[]): RoundGroup[] {
  return rounds.map((round) => {
    const turns = transcript
      .filter((t) => t.round_n === round.round_n && t.scoring)
      .sort((a, b) => Number(a.actor.slice(1)) - Number(b.actor.slice(1)));
    return { round, turns, revealed: round.truth !== null };
  });
}

/** Each seat's points for a round: its answer's marks plus whatever the calls paid it. */
export function roundTotals({ round, turns }: RoundGroup): Record<string, number> {
  const totals: Record<string, number> = {};
  for (const t of turns) totals[t.actor] = (totals[t.actor] ?? 0) + (t.points ?? 0);
  for (const g of round.guesses) totals[g.awarded_to] = (totals[g.awarded_to] ?? 0) + g.points;
  return totals;
}

/** The seat that took a round outright, or null when the top is shared. */
export function roundWinner(group: RoundGroup): string | null {
  const ranked = Object.entries(roundTotals(group)).sort((a, b) => b[1] - a[1]);
  if (ranked.length === 0 || (ranked.length > 1 && ranked[0][1] === ranked[1][1])) return null;
  return ranked[0][0];
}

/** What the host says once the match is over: the coaching line on a loss, else a parting word. */
export function endLine(ended: MatchEnded, me: string | null): { label: string | null; text: string } {
  if (ended.coaching_line) return { label: "What would have won", text: ended.coaching_line };
  if (ended.winner === null) return { label: null, text: "Level. The dictionary keeps the last word." };
  if (ended.winner === me) return { label: null, text: "Take the win and go. The next one will not be so polite." };
  return { label: null, text: "The replay is saved. So is the lesson." };
}

/** Seconds left until a deadline, ticking once a second; null without one. */
export function useSecondsLeft(deadline: string | null): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!deadline) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [deadline]);
  if (!deadline) return null;
  return Math.max(0, Math.round((Date.parse(deadline) - now) / 1000));
}

export function clockText(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
