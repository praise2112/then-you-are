import { useState } from "react";

import { clockText, groupRounds, roundTotals, roundWinner, shortName, useSecondsLeft } from "../format.ts";
import { ResultCard } from "../ResultCard.tsx";
import { Bluff, CallCard, CallLine, RoundLedger, TruthLine, WordCard } from "../rounds.tsx";
import { SeatList, SeatStrip } from "../seats.tsx";
import { Composer, HostPanel, ResignRow, ResignSheet, RoundBar, RubricPanel, type BoardProps } from "./parts.tsx";

/** Everyone at once: one card per round, every seat writes, the call once all are judged, all
 *  answers shown together once the truth is out. */
export function ShowcaseBoard({ duel, snap, template, table }: BoardProps) {
  const { spectator, pending, returned, finished, picked, calling } = duel;
  const ended = !!duel.ended;
  const [showResign, setShowResign] = useState(false);
  const left = useSecondsLeft(snap.turn_deadline);
  const me = table.me;
  const groups = groupRounds(snap.rounds, snap.transcript);
  const revealed = groups.filter((g) => g.revealed);
  const current = groups.find((g) => !g.revealed);
  const roundN = current?.round.round_n ?? revealed[revealed.length - 1]?.round.round_n ?? 1;
  const mySeat = me ? table.seat(me) : undefined;
  const mine = current?.turns.find((t) => t.actor === me);
  const onCall = snap.phase === "guess" && !!current && current.round.options.length > 0;
  const waitingCall = snap.phase === "guess" && !onCall && !finished;
  const lastResult = snap.phase === "guess" ? undefined : revealed[revealed.length - 1];
  const earlier = lastResult ? revealed.slice(0, -1) : revealed;
  const judging = !mine && (pending || !!mySeat?.answered) && snap.phase === "write";
  const out = !!mySeat?.eliminated;
  const canWrite = snap.status === "active" && !!me && !out && !mine && !judging && !ended && !!current && snap.phase === "write";
  const clock = left !== null ? clockText(left) : null;
  const hostLine = ended
    ? "The match is over. One moment."
    : returned?.nudge_text
      ? returned.nudge_text
      : onCall
        ? calling
          ? "Called. The truth is coming out."
          : "One of these is real. The rest were written this minute."
        : waitingCall
          ? "The others are calling."
          : judging
            ? "The judge is reading yours."
            : mine
              ? "Yours is in. The rest are still writing."
              : spectator
                ? "The table is writing."
                : template.move_hint;

  return (
    <>
      <RoundBar spectator={spectator} template={template} round={roundN} />

      <main className="stage showcase">
        <div className="rail">
          <SeatList snap={snap} table={table} />
          <RoundLedger groups={groups} table={table} />
        </div>
        <section>
          <SeatStrip snap={snap} table={table} />

          {earlier.length > 0 && (
            <ol className="rounds-so-far">
              {earlier.map((group) => {
                const { round } = group;
                const totals = roundTotals(group);
                const won = roundWinner(group);
                return (
                  <li key={round.round_n}>
                    {template.medallions && (
                      <span className="medallion sm" role="img" aria-label={round.token}>
                        {round.emoji}
                      </span>
                    )}
                    <span>
                      <b>{round.token}</b> {round.truth && <em>{round.truth}</em>}
                    </span>
                    <span className="tally-line">
                      {won ? `${table.name(won)} took it, ${totals[won]}` : "shared"}
                    </span>
                  </li>
                );
              })}
            </ol>
          )}

          {lastResult && (
            <section className={`round-result${finished ? " final" : ""}`}>
              <TruthLine round={lastResult.round} />
              <div className="bluffs">
                {lastResult.turns.map((turn) => (
                  <Bluff
                    key={turn.seq}
                    turn={turn}
                    round={lastResult.round}
                    who={table.name(turn.actor)}
                    tone={table.tone(turn.actor)}
                    you={turn.actor === me}
                    ai={table.seat(turn.actor)?.kind === "model"}
                    won={roundWinner(lastResult) === turn.actor}
                    template={template}
                  />
                ))}
              </div>
              {template.guess && <CallLine group={lastResult} table={table} />}
            </section>
          )}

          {current && !finished && (
            <>
              <p className="small-caps last-move-head" style={{ marginTop: lastResult ? "var(--space-3)" : "var(--space-2)" }}>
                {lastResult ? template.labels.next_opening : template.labels.opening}
              </p>
              <WordCard round={current.round} compact={revealed.length > 0}>
                {judging && <span className="tag">Being judged</span>}
              </WordCard>
            </>
          )}

          {mine && snap.phase === "write" && (
            <div className="entry-mine">
              <span className={`who tone-${table.tone(mine.actor)} you`}>
                <span>You wrote</span>
              </span>
              <p>
                <b>{current?.round.token}</b> {mine.move_text}
              </p>
            </div>
          )}
          {(mine || judging) && snap.phase === "write" && !finished && (
            <p className="waiting">The meanings show when everyone has written{clock ? `, or in ${clock}` : ""}.</p>
          )}

          {onCall && current && (
            <CallCard
              round={current.round}
              template={template}
              picked={picked}
              onPick={duel.setPicked}
              onCall={duel.call}
              pending={calling}
              clock={clock}
            />
          )}
          {waitingCall && <p className="waiting">Waiting for the others to call{clock ? `, ${clock} left` : ""}.</p>}
          {onCall && returned && (
            <p className="why">
              <b>The call did not land.</b> {returned.reason_text}
            </p>
          )}

          {finished && <ResultCard snap={snap} ended={finished} table={table} />}
          {!spectator && me && current && !onCall && !mine && !judging && !out && !finished && snap.phase === "write" && (
            <Composer
              duel={duel}
              template={template}
              label={template.labels.compose.replace("{token}", shortName(current.round.token))}
              disabled={pending || ended}
              placeholder={template.move_example}
              refusal="Not a definition yet."
              clock={clock}
              canSend={canWrite}
            />
          )}
          {out && !finished && <p className="waiting">You are out of this match. The others play on.</p>}

          {!spectator && !finished && me && !out && <ResignRow disabled={ended} onAsk={() => setShowResign(true)} />}
        </section>

        <section>
          <RubricPanel template={template} open={revealed.length === 0} />
          <ul className="rules-text">
            {template.rules.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
          <HostPanel line={hostLine} thinking={judging} finished={finished} me={me} />
        </section>
      </main>

      {showResign && (
        <ResignSheet
          duel={duel}
          text={snap.seats.length === 2 ? "The other seat takes the win." : "You leave the table and the others play on."}
          onClose={() => setShowResign(false)}
        />
      )}
    </>
  );
}
