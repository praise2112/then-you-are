import { useEffect, useRef, useState, type ReactNode } from "react";

import type { GuessOption, RoundView, TemplateView, TurnView } from "../api.ts";
import { criterionLabel, HOUSE, isLongCard, roundTotals, roundWinner, type RoundGroup } from "./format.ts";

const BADGE_LABELS: Record<string, string> = {
  close_call: "Close call",
  accidental_truth: "Accidental truth",
  near_miss: "Near miss",
};

type WordCardProps = { round: RoundView; compact?: boolean; children?: ReactNode };

export function WordCard({ round, compact = false, children }: WordCardProps) {
  return (
    <div className={`torn word-card${compact ? " compact" : ""}${isLongCard(round.token) ? " long" : ""}`}>
      {children}
      <p className="headword">{round.token}</p>
      {round.detail && <p className="detail">{round.detail}</p>}
    </div>
  );
}

/** "adjective, from Latin" gives "adjective"; the short form is used inside a bluff entry. */
export function posOf(detail: string): string {
  return detail.split(",")[0].trim();
}

const POS_SHORT: Record<string, string> = {
  noun: "n.",
  verb: "v.",
  adjective: "adj.",
  adverb: "adv.",
  interjection: "interj.",
};

/** The reveal, set as the real dictionary entry for the round's word. */
export function TruthLine({ round }: { round: RoundView }) {
  if (!round.truth) return null;
  return (
    <p className="entry">
      <span className="medallion sm" role="img" aria-label={round.token}>
        {round.emoji}
      </span>
      <span className="headword">{round.token}</span>
      <span className="pos">{posOf(round.detail)}</span>
      <span className="sense">{round.truth}</span>
    </p>
  );
}

type CallCardProps = {
  round: RoundView;
  template: TemplateView;
  picked: string | null;
  onPick: (key: string) => void;
  onCall: () => void;
  pending: boolean;
  spectator?: boolean;
  who?: string;
};

/** The call: the other side's bluff and the real entry, in an order that gives nothing away. */
export function CallCard({ round, template, picked, onPick, onCall, pending, spectator = false, who = "You" }: CallCardProps) {
  const pos = posOf(round.detail);
  return (
    <div className="call-card">
      <p className="small-caps last-move-head">{spectator ? `${who} to call` : "Your call"}</p>
      <p className="call-prompt">{template.guess?.prompt}</p>
      <div className="call-options" role={spectator ? undefined : "radiogroup"} aria-label="The entries on the table">
        {round.options.map((option: GuessOption) => (
          <button
            key={option.key}
            type="button"
            className={`call-option${picked === option.key ? " picked" : ""}`}
            role={spectator ? undefined : "radio"}
            aria-checked={spectator ? undefined : picked === option.key}
            disabled={spectator || pending}
            onClick={() => onPick(option.key)}
          >
            <span className="said">
              <b>{round.token}</b> <i>{POS_SHORT[pos] ?? pos}</i> {option.text}
            </span>
            {picked === option.key && <span className="stamp point">The real one</span>}
          </button>
        ))}
      </div>
      {!spectator && (
        <div className="composer-foot">
          <span className="counter">
            {template.guess?.spot_points} points to whoever is right about it
          </span>
          <button className={`ticket${picked && !pending ? "" : " quiet"}`} type="button" disabled={!picked || pending} onClick={onCall}>
            {pending ? "Called" : "Call it"}
          </button>
        </div>
      )}
    </div>
  );
}

type CallLineProps = { group: RoundGroup; me: string; theirs?: string };

/** How the call went: who was fooled, who saw through it, and what it paid. */
export function CallLine({ group, me, theirs = HOUSE }: CallLineProps) {
  const name = (actor: string) => (actor === "p1" ? me : theirs);
  if (group.round.guesses.length === 0) {
    return <p className="call-line">Nothing to call this round. {theirs} had written the real meaning.</p>;
  }
  return (
    <>
      {group.round.guesses.map((g) => (
        <p key={g.actor} className={`call-line${g.awarded_to === "p1" ? " mine" : ""}`}>
          {g.picked === "truth" ? (
            <>
              <b>{name(g.actor)} called the real entry.</b> {g.points} points.
            </>
          ) : (
            <>
              <b>{name(g.actor)} took the bait.</b> {name(g.picked)}&rsquo;s bluff read as the real thing, {g.points} points to{" "}
              {name(g.awarded_to)}.
            </>
          )}
        </p>
      ))}
    </>
  );
}

type LedgerProps = { groups: RoundGroup[]; me: string };

/** Every round so far: the word, its truth once revealed, and who took the points. */
export function RoundLedger({ groups, me }: LedgerProps) {
  return (
    <section className="ledger" aria-label="The rounds">
      <p className="small-caps">The rounds</p>
      <ol>
        {groups.map((group) => {
          const { round, revealed } = group;
          const won = roundWinner(group);
          const totals = roundTotals(group);
          return (
            <li key={round.round_n} className={revealed ? undefined : "open"}>
              <b className="word">{round.token}</b>
              {revealed ? (
                <>
                  {round.truth && <span className="truth">{round.truth}</span>}
                  <span className="tally-line">
                    <span className={won === "mine" ? "took" : undefined}>{totals.mine}</span>
                    {" : "}
                    <span className={won === "theirs" ? "took" : undefined}>{totals.theirs}</span>
                    <em>
                      {won === null ? "shared" : won === "mine" ? `${me} took it` : `${HOUSE} took it`}
                    </em>
                  </span>
                </>
              ) : (
                <span className="truth">{round.options.length > 0 ? "your call" : "in play"}</span>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** The judge's reason, folded away. Opens on hover or a click, closes on a click away. */
function Why({ text }: { text: string }) {
  const [stuck, setStuck] = useState(false);
  const [hovered, setHovered] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!stuck) return;
    const away = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setStuck(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setStuck(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [stuck]);
  const open = stuck || hovered;
  return (
    <span
      className="why"
      ref={ref}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        className="why-mark"
        aria-expanded={open}
        aria-label="Why this score"
        onClick={() => setStuck((on) => !on)}
      >
        ?
      </button>
      {open && <span className="why-note">{text}</span>}
    </span>
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

type BluffProps = {
  turn: TurnView;
  round: RoundView;
  who: string;
  you?: boolean;
  won?: boolean;
  template: TemplateView;
};

/** One bluff set as a dictionary entry that did not make it, with its marks and the judge's reason. */
export function Bluff({ turn, round, who, you = false, won = false, template }: BluffProps) {
  const totalAvailable = template.rubric.reduce((sum, r) => sum + r.max_points, 0);
  const pos = posOf(round.detail);
  return (
    <article className={`bluff${won ? " won" : ""}`}>
      <p className="for">
        <span className={`who${you ? " you" : ""}`}>{who} wrote</span>
        {won && <span className="stamp point">Point</span>}
      </p>
      <p className="said">
        <b>{round.token}</b> {pos && <i>{POS_SHORT[pos] ?? pos}</i>} {turn.move_text}
      </p>
      <p className="marks">
        {template.rubric.map((entry) => {
          const earned = (turn.scoring?.scores[entry.name] ?? 0) * (entry.max_points / template.score_max);
          const decided = entry.name === turn.host?.because_clause.criterion;
          return (
            <span key={entry.name} className={decided ? "decided" : undefined}>
              <span className="small-caps">{criterionLabel(entry.name, template)}</span>
              <span className="value">
                {earned}/{entry.max_points}
              </span>
            </span>
          );
        })}
      </p>
      <div className="foot">
        <span className="total">
          {turn.points ?? 0}
          <small>/{totalAvailable}</small>
        </span>
        <Why text={turn.host?.because_clause.text ?? ""} />
      </div>
      <Badges turn={turn} />
    </article>
  );
}
