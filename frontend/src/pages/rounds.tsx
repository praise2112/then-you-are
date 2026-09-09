import type { ReactNode } from "react";

import type { RoundView, TemplateView, TurnView } from "../api.ts";
import { criterionLabel } from "./format.ts";

const BADGE_LABELS: Record<string, string> = {
  close_call: "Close call",
  accidental_truth: "Accidental truth",
  near_miss: "Near miss",
};

type WordCardProps = { round: RoundView; compact?: boolean; children?: ReactNode };

export function WordCard({ round, compact = false, children }: WordCardProps) {
  return (
    <div className={`torn word-card${compact ? " compact" : ""}`}>
      {children}
      <p className="headword">{round.token}</p>
      <p className="detail">{round.detail}</p>
    </div>
  );
}

type TruthLineProps = { round: RoundView; turns?: (TurnView | undefined)[]; hostName?: string };

/** The reveal: the card's truth, and one host line for the round, from the bluff that scored higher. */
export function TruthLine({ round, turns = [], hostName }: TruthLineProps) {
  const best = turns
    .filter((t): t is TurnView => !!t?.host)
    .sort((a, b) => (b.points ?? 0) - (a.points ?? 0))[0];
  return (
    <div className="truth-line">
      <span className="medallion sm" role="img" aria-label={round.token}>
        {round.emoji}
      </span>
      <span>
        <b>What {round.token} really means:</b> {round.truth}
        {best?.host && hostName && (
          <em className="host-says">
            <span className="small-caps">{hostName}</span> {best.host.headline}
          </em>
        )}
      </span>
    </div>
  );
}

export function Badges({ turn }: { turn: TurnView }) {
  const badges = turn.host?.badges ?? [];
  return (
    <p className="badges">
      {turn.outcome === "fail" && <span className="stamp ink">Thrown out</span>}
      {badges.map((b) => (
        <span key={b} className="stamp">
          {BADGE_LABELS[b] ?? criterionLabel(b)}
        </span>
      ))}
    </p>
  );
}

type BluffProps = { turn: TurnView; who: string; you?: boolean; template: TemplateView };

/** One player's answer to a card with its ruling. Rows share a subgrid so two side by side line up. */
export function Bluff({ turn, who, you = false, template }: BluffProps) {
  const totalAvailable = template.rubric.reduce((sum, r) => sum + r.max_points, 0);
  return (
    <article className="bluff">
      <p className="for">
        <span className={`who${you ? " you" : ""}`}>{who}</span>
        {turn.scoring && <span>The judge was {turn.scoring.confidence}</span>}
      </p>
      <p className="said">{turn.move_text}</p>
      <p className="headline">{turn.host?.because_clause.text}</p>
      <p className="marks">
        {template.rubric.map((entry) => {
          const earned = (turn.scoring?.scores[entry.name] ?? 0) * (entry.max_points / template.score_max);
          const decided = entry.name === turn.host?.because_clause.criterion;
          return (
            <span key={entry.name} className={decided ? "decided" : undefined}>
              <span className="small-caps">{criterionLabel(entry.name)}</span> <b>{earned}</b>
              <small>/{entry.max_points}</small>
            </span>
          );
        })}
      </p>
      <p className="total">
        <span>This round</span>
        <b>
          {turn.points ?? 0} of {totalAvailable}
        </b>
      </p>
      <Badges turn={turn} />
    </article>
  );
}
