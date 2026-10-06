import { useEffect, useRef, useState } from "react";

import { api } from "../../api.ts";
import { Icon } from "../../Icons.tsx";
import {
  capitalize,
  clockText,
  criterionLabel,
  formName,
  isLive,
  fullMove,
  isLongCard,
  isLongMove,
  judgeFace,
  lastStanding,
  shortName,
  useSecondsLeft,
} from "../format.ts";
import { criterionMarks, totalPoints, verdictLabel } from "../labels.ts";
import { ResultCard } from "../ResultCard.tsx";
import { SeatList, SeatStrip } from "../seats.tsx";
import { Composer, HostPanel, MoveSlip, ResignRow, ResignSheet, RoundBar, RubricPanel, type BoardProps } from "./parts.tsx";

/** Turn by turn: the seats in the rail, the standing move in the middle, the move box under it. */
export function EscalationBoard({ duel, snap, template, table }: BoardProps) {
  const { spectator, text, pending, thinking, streaming, returned, paused, ended, finished } = duel;
  const [showResign, setShowResign] = useState(false);
  const [showRubric, setShowRubric] = useState(false);
  const [voted, setVoted] = useState<Set<number>>(new Set());
  const transcriptRef = useRef<HTMLOListElement>(null);
  // A new move scrolls the transcript to it; opening the match leaves the transcript as it is.
  const moves = snap.transcript.length;
  const scrolledFor = useRef(moves);
  useEffect(() => {
    const el = transcriptRef.current;
    if (moves === scrolledFor.current || !el) return;
    scrolledFor.current = moves;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [moves]);
  const disagree = (seq: number) => void api.disagree(snap.id, seq).then(() => setVoted((v) => new Set(v).add(seq)));
  const left = useSecondsLeft(snap.turn_deadline);
  const me = table.me;
  const prefix = template.move_prefix;
  const standing = lastStanding(snap.transcript);
  const judged = snap.transcript.filter((t) => t.scoring);
  const latest = judged[judged.length - 1];
  const fell = ended && latest?.outcome === "fail" ? latest : null;
  const shown = fell ?? standing;
  const face = shown && judgeFace(shown);
  const yourLast = [...snap.transcript].reverse().find((t) => t.actor === me && t.host);
  const showYourSlip = yourLast && latest?.actor !== me && !streaming && !finished;
  const standingText = standing?.move_text || snap.seed_token;
  const cardText = fell ? fell.move_text : standingText;
  const standingIsMine = !!me && standing?.actor === me;
  const live = isLive(snap.status);
  const moverName = table.name(snap.to_move);
  const canPlay = snap.status === "active" && !!me && snap.to_move === me && !pending && !paused && !ended;
  const round = Math.min(snap.round_in_play, template.rounds_budget);
  const myIndex = snap.seats.findIndex((s) => s.seat === me);
  const after = snap.seats.filter((s, i) => i > myIndex && !s.eliminated).map((s) => table.name(s.seat));
  const lastRound = round === template.rounds_budget && snap.to_move === me && !!me;
  const longCard = isLongCard(snap.seed_token);
  const standingName = longCard && !standing ? template.labels.opening.toLowerCase() : shortName(formName(standingText, prefix));
  const other = snap.seats.find((s) => s.seat !== me);

  const slip = slipOnTable();
  const withJudge = slip?.state === "read" || slip?.state === "held";
  const seatOrder = snap.seats.filter((s) => !s.eliminated).map((s) => s.seat);

  // The move the table is waiting on: being written, or with the judge.
  function slipOnTable() {
    if (!live || ended) return null;
    const name = snap.to_move === me ? "Yours" : moverName;
    const tone = table.tone(snap.to_move);
    if (paused) return { name, tone, state: "held" as const, text: paused.move_text, note: "The match is paused until the judge rules." };
    if (snap.status === "awaiting_judgment" || thinking || (pending && snap.to_move === me)) {
      const mine = snap.to_move === me && text ? fullMove(prefix, text) : null;
      return { name, tone, state: "read" as const, text: mine ?? (streaming || null) };
    }
    if (snap.to_move !== me) return { name, tone, state: "write" as const, text: streaming };
    return null;
  }

  function untilYourMove(): string {
    if (!me || !seatOrder.includes(me)) return "";
    if (round === template.rounds_budget && snap.seats.findIndex((s) => s.seat === snap.to_move) >= myIndex) {
      return "Your last move is in. The match ends with this round.";
    }
    const between = [];
    for (let k = seatOrder.indexOf(snap.to_move) + 1; seatOrder[k % seatOrder.length] !== me; k++) between.push(seatOrder[k % seatOrder.length]);
    if (between.length === 0) return "Your move is next.";
    if (between.length > 2) return `You write again after ${between.length} more moves.`;
    return `You write again after ${between.map((s) => table.name(s).replace(/^The /, "the ")).join(" and ")}.`;
  }

  function standingHead(): string {
    if (fell) return `${table.name(fell.actor)} fell`;
    if (finished) return standing ? `${table.name(standing.actor)} had the last word` : template.labels.opening;
    if (!standing) return template.labels.opening;
    return standingIsMine ? "You wrote" : `From ${table.name(standing.actor)}`;
  }

  function composerLabel(): string {
    if (paused) return template.labels.compose_waiting;
    if (returned) return "Your move, still yours";
    return template.labels.compose.replace("{token}", standingName);
  }

  function hostLine(): string {
    if (fell) return "The match is over. One moment.";
    if (paused) return paused.host_text;
    if (returned?.nudge_text) return returned.nudge_text;
    if (canPlay) return `${capitalize(standingName)}. ${template.move_hint}`;
    return `${capitalize(standingName)} stands.`;
  }

  return (
    <>
      <RoundBar spectator={spectator} template={template} round={round} />

      <main className="stage">
        <section>
          <SeatList snap={snap} table={table} withJudge={withJudge} />
          <p className="centered-label small-caps" style={{ marginTop: "var(--space-3)" }}>
            The match so far
          </p>
          <ol className="transcript" ref={transcriptRef}>
            <li>
              <div className="move">
                <span>
                  <span className="who">{template.labels.opening}</span>
                  {snap.seed_token}
                </span>
                {template.medallions && (
                  <span className="medallion sm" role="img" aria-label={snap.seed_token}>
                    {snap.seed_emoji}
                  </span>
                )}
              </div>
            </li>
            {snap.transcript.map((turn) => (
              <li key={turn.seq}>
                <div className="move">
                  <span>
                    <span className={`who tone-${table.tone(turn.actor)}${turn.actor === me ? " you" : ""}`}>
                      <span>{table.name(turn.actor)}</span>
                    </span>
                    {turn.outcome === "forfeit" ? <em>{verdictLabel(turn, "line")}</em> : turn.move_text}
                  </span>
                  {template.medallions && turn.outcome !== "forfeit" && (
                    <span className="medallion sm" role="img" aria-label={formName(turn.move_text, prefix)}>
                      {turn.host?.generated_emoji ?? "?"}
                    </span>
                  )}
                </div>
                {turn.outcome !== "forfeit" && (
                  <p className="ruling">
                    <span className="word">{verdictLabel(turn, "line")}</span>
                    <em>{turn.host?.headline}</em>
                  </p>
                )}
              </li>
            ))}
            {paused && (
              <li>
                <div className="move">
                  <span>
                    <span className={snap.to_move === me ? "who you" : `who tone-${table.tone(snap.to_move)}`}>
                      <span>{table.name(snap.to_move)}</span>
                    </span>
                    {paused.move_text}
                  </span>
                  {template.medallions && <span className="medallion sm empty">?</span>}
                </div>
                <p className="ruling">
                  <em>Waiting for the judge</em>
                </p>
              </li>
            )}
          </ol>
          {!spectator && !finished && me && <ResignRow disabled={!!ended} onAsk={() => setShowResign(true)} />}
        </section>

        <section>
          <SeatStrip snap={snap} table={table} withJudge={withJudge} />
          {showYourSlip && !spectator && (
            <p className="your-slip">
              {template.medallions && (
                <span className="medallion" aria-hidden="true">
                  {yourLast.host?.generated_emoji}
                </span>
              )}
              <span>
                <b>
                  {verdictLabel(yourLast, "line")} for {formName(yourLast.move_text, prefix)}.
                </b>{" "}
                <q>{yourLast.host?.headline}</q>
              </span>
            </p>
          )}

          {longCard && (
            <div className="torn card-block">
              <p className="small-caps">{template.labels.opening}</p>
              <p className="card-text">{snap.seed_token}</p>
            </div>
          )}
          {(!longCard || shown) && (
            <>
              <p className="small-caps last-move-head">{standingHead()}</p>
              <div className={`torn standing${finished ? " final" : ""}${slip && isLongMove(cardText) ? " under" : ""}`}>
                {shown && (
                  <span className={`stamp corner${shown.outcome === "accept" ? "" : " ink"}`}>{verdictLabel(shown, "standing", prefix)}</span>
                )}
                <p className={`last-move${isLongMove(cardText) ? " long" : ""}`}>{cardText}</p>
                {shown?.host && (
                  <div className="ruled">
                    <span className={`judge-face${face?.fall ? " fall" : ""}`} aria-hidden="true">
                      {face?.face}
                    </span>
                    <div>
                      <p className="headline">{shown.host.headline}</p>
                      <p className="because">
                        {criterionLabel(shown.host.because_clause.criterion, template)}: {shown.host.because_clause.text}
                      </p>
                      <button className="vote" type="button" disabled={voted.has(shown.seq)} onClick={() => disagree(shown.seq)}>
                        <Icon name="speech" />
                        {voted.has(shown.seq) ? "Noted. The ruling stands." : "I disagree with this ruling"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
          {slip && <MoveSlip key={snap.to_move} {...slip} />}

          {finished && <ResultCard snap={snap} ended={finished} table={table} />}
          {!spectator && me && (canPlay || (paused && !ended)) && (
            <Composer
              duel={duel}
              template={template}
              label={composerLabel()}
              prefix={prefix}
              disabled={!canPlay && !paused}
              placeholder={paused ? "thinking ahead. It sends when play resumes." : template.move_example.slice(prefix.length)}
              refusal="Not a move yet."
              clock={canPlay && left !== null ? clockText(left) : null}
              canSend={canPlay}
            >
              {lastRound && (
                <p className="last-round">
                  {after.length > 0
                    ? `Last round: ${after.join(", ")} ${after.length === 1 ? "answers" : "answer"} once more, then it’s over.`
                    : "Last round: your move is the last of the match."}
                </p>
              )}
            </Composer>
          )}
          {!spectator && me && slip && !paused && <p className="waiting">{untilYourMove()}</p>}
        </section>

        <section>
          {latest?.scoring && !showRubric ? (
            <>
              <p className="panel-head">
                <span className="small-caps">Last ruling, {formName(latest.move_text, prefix)}</span>
                <button className="swap small-caps" type="button" onClick={() => setShowRubric(true)}>
                  Scoring rules
                </button>
              </p>
              <div className="scorecard">
                <p className="for">
                  <span>{latest.actor === me ? "Your move" : `${table.name(latest.actor)}'s move`}</span>
                  <span>The judge was {latest.scoring.confidence}</span>
                </p>
                <dl className="points">
                  {criterionMarks(latest, template).map(({ entry, earned, decided }) => (
                    <div key={entry.name} className={decided ? "decided" : undefined}>
                      <dt>
                        {criterionLabel(entry.name, template)}
                        {decided && <span className="decided-tag">decided it</span>}
                      </dt>
                      <dd>
                        <b>{earned}</b> of {entry.max_points}
                      </dd>
                    </div>
                  ))}
                </dl>
                <p className="total">
                  <span>This move</span>
                  <b>
                    {latest.points ?? 0} of {totalPoints(template)}
                  </b>
                </p>
              </div>
            </>
          ) : (
            <RubricPanel template={template} open>
              {latest?.scoring && (
                <button className="swap small-caps" type="button" onClick={() => setShowRubric(false)}>
                  Last ruling
                </button>
              )}
            </RubricPanel>
          )}
          <HostPanel line={hostLine()} thinking={!!paused} finished={finished} me={me} />
        </section>
      </main>

      {showResign && (
        <ResignSheet
          duel={duel}
          text={
            snap.seats.length === 2 && other
              ? `${table.name(other.seat)} takes the win at ${(me && table.seat(me)?.points) ?? 0} : ${other.points}.`
              : "You leave the table and the others play on."
          }
          onClose={() => setShowResign(false)}
        />
      )}
    </>
  );
}
