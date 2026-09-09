import type { ReactNode } from "react";

import type { RoundView, TemplateView, TurnView } from "../api.ts";
import { criterionLabel } from "./format.ts";

const BADGE_LABELS: Record<string, string> = {
  close_call: "Close call",
  accidental_truth: "Accidental truth",
  near_miss: "Near miss",
};

export function WordCard({ round, children }: { round: RoundView; children?: ReactNode }) {
  return (
    <div className="torn word-card">
      {children}
      <p className="headword">{round.token}</p>
      <p className="detail">{round.detail}</p>
    </div>
  );
}

export function TruthLine({ round }: { round: RoundView }) {
  return (
    <p className="truth-line">
      <span className="medallion sm" role="img" aria-label={round.token}>
        {round.emoji}
      </span>
      <span>
        <b>What {round.token} really means:</b> {round.truth}
      </span>
    </p>
  );
}

export function Badges({ turn }: { turn: TurnView }) {
  const badges = turn.host?.badges ?? [];
  if (turn.outcome !== "fail" && badges.length === 0) return null;
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

/** One player's answer to a card with its ruling: the sentence, the headline, and the scorecard. */
export function Bluff({ turn, who, you = false, template }: BluffProps) {
  const totalAvailable = template.rubric.reduce((sum, r) => sum + r.max_points, 0);
  return (
    <article className="bluff scorecard">
      <p className="for">
        <span className={`who${you ? " you" : ""}`}>{who}</span>
        {turn.scoring && <span>The judge was {turn.scoring.confidence}</span>}
      </p>
      <p className="said">{turn.move_text}</p>
      {turn.host && <p className="headline">{turn.host.headline}</p>}
      <dl className="points">
        {template.rubric.map((entry) => {
          const earned = (turn.scoring?.scores[entry.name] ?? 0) * (entry.max_points / template.score_max);
          const decided = entry.name === turn.host?.because_clause.criterion;
          return (
            <div key={entry.name} className={decided ? "decided" : undefined}>
              <dt>{criterionLabel(entry.name)}</dt>
              <dd>
                <b>{earned}</b> of {entry.max_points}
              </dd>
            </div>
          );
        })}
      </dl>
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
