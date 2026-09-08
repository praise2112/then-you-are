import { useState } from "react";

import type { Ruling, TemplateView } from "../api.ts";
import { Host } from "../Host.tsx";
import { Icon } from "../Icons.tsx";
import { criterionLabel, formName } from "./format.ts";

type Props = {
  ruling: Ruling;
  previous: string;
  opponentName: string;
  template: TemplateView;
  ended: boolean;
  onNext: () => void;
  onDisagree: () => Promise<void>;
};

export function VerdictSheet({ ruling, previous, opponentName, template, ended, onNext, onDisagree }: Props) {
  const [voted, setVoted] = useState(false);
  const mine = ruling.actor === "p1";
  const closeCall = ruling.outcome === "semantic_uncertain";
  const fail = ruling.outcome === "fail";
  const name = formName(ruling.move_text);

  const stamp = closeCall ? "Close call" : fail ? "The form breaks" : `Point: ${name}`;
  const whose = closeCall
    ? "The move stands. Match goes on."
    : fail
      ? mine
        ? "Your form fell."
        : `${opponentName}'s form fell. You win.`
      : mine
        ? "Your point"
        : `${opponentName}'s point`;
  const confidence = closeCall
    ? "The judge could not decide. A close call never ends a match and never costs anyone the round."
    : `The judge was ${ruling.scoring.confidence} about this one.`;
  const nextLabel = ended ? "See the result" : mine ? `${opponentName} moves next` : "Your move";
  const deciding = ruling.host.because_clause.criterion;

  return (
    <div className="scrim">
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="ruling headline">
        <div className="sheet-head">
          <div>
            <span className={`stamp${closeCall || (fail && mine) ? " ink" : ""}`} id="ruling">
              {stamp}
            </span>
            <p className="whose small-caps">{whose}</p>
          </div>
          <figure className="verdict-emoji">
            <span className="medallion">{ruling.host.generated_emoji}</span>
            <figcaption>{name}</figcaption>
          </figure>
        </div>

        <dl className="evidence">
          <dt>{mine ? "Standing" : "You became"}</dt>
          <dd className={mine ? undefined : "own-move"}>{previous}</dd>
          <dt>{mine ? "You became" : `${opponentName} became`}</dt>
          <dd className={mine ? "own-move" : undefined}>{ruling.move_text}</dd>
        </dl>

        <div className="host" style={{ marginTop: "var(--space-3)" }}>
          <Host state={closeCall ? "thinking" : "verdict"} />
          <div>
            <h2 className="headline" id="headline">
              {ruling.host.headline}
            </h2>
            <p className="because">
              <b>{criterionLabel(deciding)}:</b> {ruling.host.because_clause.text}
            </p>
            <p className="confidence">{confidence}</p>
          </div>
        </div>

        <table className="scores">
          <caption className="small-caps">How it scored</caption>
          <tbody>
            {template.rubric.map((entry) => {
              const value = ruling.scoring.scores[entry.name] ?? 0;
              return (
                <tr key={entry.name} className={entry.name === deciding ? "deciding" : undefined}>
                  <th scope="row" className="name">
                    {criterionLabel(entry.name)}
                  </th>
                  <td>
                    <span
                      className="bar"
                      role="meter"
                      aria-label={criterionLabel(entry.name)}
                      aria-valuenow={value}
                      aria-valuemin={0}
                      aria-valuemax={4}
                    >
                      <i style={{ width: `${(value / 4) * 100}%` }}></i>
                    </span>
                  </td>
                  <td className="value">{value}/4</td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <div className="sheet-foot" style={{ marginTop: "var(--space-3)" }}>
          <div>
            <button
              className="vote"
              type="button"
              disabled={voted}
              onClick={() => onDisagree().then(() => setVoted(true))}
            >
              <Icon name="speech" />
              {voted ? "Noted. The ruling stands." : "I disagree with this ruling"}
            </button>
            <p className="foot-hint">
              {closeCall
                ? "Close calls are where your vote teaches the judge most."
                : "The ruling stands. Your vote goes to improving the judge."}
            </p>
          </div>
          <button className="ticket" type="button" onClick={onNext} autoFocus>
            {nextLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
