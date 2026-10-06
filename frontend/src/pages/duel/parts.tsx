import { useRef, type FormEvent, type KeyboardEvent, type ReactNode } from "react";

import { ThemeToggle, TopBar } from "../../App.tsx";
import type { MatchEnded, MatchSnapshot, TemplateView } from "../../api.ts";
import { Host } from "../../Host.tsx";
import { Icon } from "../../Icons.tsx";
import { criterionLabel, endLine, fullMove, isLongMove, type Table } from "../format.ts";
import type { DuelState } from "./useDuel.ts";

export type BoardProps = { duel: DuelState; snap: MatchSnapshot; template: TemplateView; table: Table };

type RoundBarProps = { spectator: boolean; template: TemplateView; round: number };

/** The top bar during play: the game and the round in play. */
export function RoundBar({ spectator, template, round }: RoundBarProps) {
  return (
    <TopBar>
      <span className="round">
        {spectator && "Watching "}
        <b>{template.title}</b>, round {round} of {template.rounds_budget}
      </span>
      <ThemeToggle />
    </TopBar>
  );
}

type ComposerProps = {
  duel: DuelState;
  template: TemplateView;
  label: string;
  // Escalation shows the fixed start of every move; showcase has none.
  prefix?: string;
  disabled: boolean;
  placeholder: string;
  refusal: string;
  clock: string | null;
  canSend: boolean;
  children?: ReactNode;
};

/** The move box: what the seat writes, why the last one came back, the clock and the send button.
 *  Enter sends; Shift+Enter breaks the line. */
export function Composer({ duel, template, label, prefix, disabled, placeholder, refusal, clock, canSend, children }: ComposerProps) {
  const formRef = useRef<HTMLFormElement>(null);
  const { text, setText, returned, pending } = duel;

  function play(event: FormEvent) {
    event.preventDefault();
    if (!canSend || !text.trim()) return;
    void duel.move();
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    formRef.current?.requestSubmit();
  }

  return (
    <form ref={formRef} className={`composer${returned ? " returned" : ""}`} style={{ marginTop: "var(--space-3)" }} onSubmit={play}>
      {returned && (
        <span className="slip returned-slip" aria-hidden="true">
          Returned, try again
        </span>
      )}
      <label className="small-caps" htmlFor="move">
        {label}
      </label>
      {children}
      <div className={prefix === undefined ? "compose-box bare" : "compose-box"}>
        {prefix !== undefined && (
          <span className="prefix" aria-hidden="true">
            {prefix}
          </span>
        )}
        <textarea
          id="move"
          autoComplete="off"
          data-form-type="other"
          data-lpignore="true"
          data-1p-ignore=""
          maxLength={template.max_chars - (prefix?.length ?? 0)}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          disabled={disabled}
          placeholder={placeholder}
        />
      </div>
      {returned && (
        <p className="why">
          <b>{refusal}</b> {returned.reason_text}
        </p>
      )}
      <div className="composer-foot">
        <span className="counter" aria-live="polite">
          {clock && <b className="clock">{clock} left · </b>}
          {prefix === undefined ? text.length : fullMove(prefix, text).length}/{template.max_chars}
        </span>
        <button className={`ticket${canSend ? "" : " quiet"}`} type="submit" disabled={!canSend}>
          {pending ? "Sent" : "Play it"}
        </button>
      </div>
    </form>
  );
}

type SlipProps = {
  name: string;
  tone: number;
  // "held" is a move the judge has paused on.
  state: "write" | "read" | "held" | "in";
  // Null while the move is hidden from this viewer.
  text: string | null;
  note?: string;
};

const SLIP_VERB = { write: "is writing", read: "with the judge", held: "with the judge", in: "in" };

/** The move being written or judged, on the table under the card in its seat's colour. */
export function MoveSlip({ name, tone, state, text, note }: SlipProps) {
  const reading = state === "read" || state === "held";
  return (
    <div className={`move-slip tone-${tone} ${state}`} role="status">
      <p className="slip-head">
        <span className={`who tone-${tone}`}>
          <span>{name}</span>
        </span>
        <em>{SLIP_VERB[state]}</em>
      </p>
      <p className={`slip-text${text && isLongMove(text) ? " long" : ""}`}>
        {reading ? (
          <span className={`read-line${state === "held" ? " held" : ""}${text ? "" : " sealed"}`}>{text ?? "Hidden until the judge rules."}</span>
        ) : (
          text
        )}
        {state === "write" && <span className="caret" />}
      </p>
      {note && <p className="slip-note">{note}</p>}
    </div>
  );
}

/** The rubric under a "Scoring rules" head; children sit in the head beside it. */
export function RubricPanel({ template, open, children }: { template: TemplateView; open: boolean; children?: ReactNode }) {
  return (
    <>
      <p className="panel-head">
        <span className="small-caps">Scoring rules</span>
        {children}
      </p>
      <div className="rubric">
        {template.rubric.map((entry) => (
          <details key={entry.name} open={open}>
            <summary>
              {criterionLabel(entry.name, template)} <small>up to {entry.max_points}</small>
            </summary>
            <p>{entry.description}</p>
          </details>
        ))}
      </div>
    </>
  );
}

type HostPanelProps = { line: string; thinking: boolean; finished: MatchEnded | null; me: string | null };

/** The host and its line. Once the match is over it gives the coaching line or a parting word. */
export function HostPanel({ line, thinking, finished, me }: HostPanelProps) {
  const parting = finished && endLine(finished, me);
  return (
    <div className="host" style={{ marginTop: "var(--space-3)" }}>
      <Host state={finished ? "tipping" : thinking ? "thinking" : "idle"} />
      <p className={`host-line${parting?.label ? " coaching" : ""}`}>
        {parting?.label && <span>{parting.label}</span>}
        {parting ? parting.text : line}
      </p>
    </div>
  );
}

export function ResignRow({ disabled, onAsk }: { disabled: boolean; onAsk: () => void }) {
  return (
    <p className="resign-row">
      <button className="quiet-button" type="button" onClick={onAsk} disabled={disabled}>
        <Icon name="flag" />
        Resign the match
      </button>
    </p>
  );
}

/** Asks before resigning; text says what happens to the table. */
export function ResignSheet({ duel, text, onClose }: { duel: DuelState; text: string; onClose: () => void }) {
  return (
    <div className="scrim">
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="give-up-title" style={{ maxWidth: "26rem" }}>
        <h2 id="give-up-title" style={{ margin: 0, fontSize: "1.8rem" }}>
          Resign this match?
        </h2>
        <p style={{ margin: "var(--space-2) 0 0", color: "var(--ink-soft)" }}>{text} The replay is saved either way.</p>
        <div className="sheet-actions">
          <button
            className="quiet-button"
            type="button"
            onClick={() => {
              onClose();
              void duel.resign();
            }}
          >
            <Icon name="flag" />
            Resign
          </button>
          <button className="ticket" type="button" onClick={onClose} autoFocus>
            Keep playing
          </button>
        </div>
      </div>
    </div>
  );
}
