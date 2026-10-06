import { useState } from "react";

import { api, type MatchSnapshot, type TemplateView } from "../api.ts";
import { clockText, isLive, useSecondsLeft, type Table } from "./format.ts";

const OFFER_HOUSE_AFTER_S = 180;

/** Marks a seat the AI plays, so nobody mistakes the House for a person. */
export function AiTag() {
  return <small className="ai-tag">AI</small>;
}

// withJudge: the move under way is with the judge.
type SeatProps = { snap: MatchSnapshot; table: Table; withJudge?: boolean };

/** One line per seat in turn order. The seat to move gets a second line and the clock
 *  bar; in a showcase round a filled dot marks a seat that has answered. */
export function SeatList({ snap, table, withJudge = false }: SeatProps) {
  const left = useSecondsLeft(snap.turn_deadline);
  const showcase = snap.mode === "showcase";
  const live = isLive(snap.status);
  return (
    <ul className="seat-list">
      {snap.seats.map((seat) => {
        const moving = live && !showcase && snap.to_move === seat.seat;
        const mine = seat.seat === table.me;
        const writing = mine ? "Your move" : left !== null ? `Writing, ${clockText(left)}` : "Writing";
        const state = moving ? (withJudge ? "With the judge" : writing) : null;
        return (
          <li
            key={seat.seat}
            className={`tone-${table.tone(seat.seat)}${moving ? " on" : ""}${seat.eliminated ? " out" : ""}${mine ? " you" : ""}${
              showcase && !seat.answered ? " pending" : ""
            }`}
          >
            <span className="who">
              <span>{table.name(seat.seat)}</span>
              {seat.kind === "model" && <AiTag />}
            </span>
            {seat.eliminated && <small>out</small>}
            <b>{seat.points}</b>
            {state && <span className="state">{state}</span>}
            {moving && left !== null && snap.clock_seconds && (
              <span className="drain">
                <i style={{ width: `${(100 * left) / snap.clock_seconds}%` }} />
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** A phone's version of the seat list: one line above the card that opens the whole list. */
export function SeatStrip({ snap, table, withJudge = false }: SeatProps) {
  const left = useSecondsLeft(snap.turn_deadline);
  const clock = left !== null ? `, ${clockText(left)}` : "";
  const mover = snap.seats.find((s) => s.seat === snap.to_move);
  let summary;
  if (snap.mode === "showcase") {
    const done = snap.seats.filter((s) => s.answered && !s.eliminated).length;
    const live = snap.seats.filter((s) => !s.eliminated).length;
    const verb = snap.phase === "guess" ? "called" : "written";
    summary = (
      <>
        <span className="dots">
          {snap.seats.map((s) => (
            <i key={s.seat} className={`tone-${table.tone(s.seat)}${s.answered ? "" : " pending"}`} />
          ))}
        </span>
        <span className="count">
          {done} of {live} {verb}
          {clock}
        </span>
      </>
    );
  } else if (mover) {
    const mine = mover.seat === table.me;
    const name = table.name(mover.seat);
    let line = mine ? "Your move" : `${name} is writing`;
    if (withJudge) line = mine ? "Yours is with the judge" : `${name}, with the judge`;
    summary = (
      <>
        <span className={`who tone-${table.tone(mover.seat)}`}>
          <span>{line}</span>
          {mover.kind === "model" && <AiTag />}
        </span>
        {!mine && !withJudge && left !== null && <b className="clock">{clockText(left)}</b>}
      </>
    );
  }
  return (
    <details className="seat-strip">
      <summary>
        {summary}
        <span className="more">All seats</span>
      </summary>
      <SeatList snap={snap} table={table} withJudge={withJudge} />
    </details>
  );
}

type WaitingProps = { snap: MatchSnapshot; template: TemplateView; table: Table; onChange: () => void };

/** A table still filling: who has sat down, the link to share, and the House as a fallback. */
export function WaitingRoom({ snap, template, table, onChange }: WaitingProps) {
  const [copied, setCopied] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const left = useSecondsLeft(snap.closes_at) ?? 0;
  const waited = (Date.parse(snap.closes_at ?? snap.created_at) - Date.parse(snap.created_at)) / 1000 - left;
  const open = snap.seats_wanted - snap.seats.length;
  const owner = snap.your_seat === "p1";
  const link = snap.invite_code ? `${location.origin}/i/${snap.invite_code}` : null;

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  async function addHouse() {
    setAdding(true);
    setError(null);
    try {
      await api.addHouse(snap.id);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="torn waiting-room">
      <h1 className="board-title">
        {template.title}
        <small>Waiting for {open} more.</small>
      </h1>
      <ul className="seat-rows">
        {snap.seats.map((seat) => (
          <li key={seat.seat} className={`tone-${table.tone(seat.seat)}${seat.seat === table.me ? " you" : ""}`}>
            <span className="who">
              <span>{table.name(seat.seat)}</span>
              {seat.kind === "model" && <AiTag />}
            </span>
            {seat.seat === "p1" && <small>Made the table</small>}
          </li>
        ))}
        {Array.from({ length: open }, (_, i) => (
          <li key={`open-${i}`} className="open">
            <span className="who">
              <span>Open seat</span>
            </span>
          </li>
        ))}
      </ul>
      {link ? (
        <div className="invite">
          <code>{link}</code>
          <button className="ticket" type="button" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      ) : (
        <p className="waiting">This table is filling. Ask a player for the invite link to take a seat.</p>
      )}
      <div className="row-actions">
        {owner && (
          <button className="ticket quiet" type="button" onClick={addHouse} disabled={adding}>
            Add the House
          </button>
        )}
        <span>Closes in {clockText(left)} if nobody joins</span>
      </div>
      {owner && waited >= OFFER_HOUSE_AFTER_S && (
        <p className="aside-note">Nobody yet. The House fills a seat and the game starts at once.</p>
      )}
      {error && <p className="hint error">{error}</p>}
    </div>
  );
}
