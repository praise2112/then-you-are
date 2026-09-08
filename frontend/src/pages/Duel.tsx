import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ThemeToggle } from "../App.tsx";
import {
  api,
  useMatchEvents,
  type JudgePaused,
  type MatchEnded,
  type MatchEvent,
  type MatchSnapshot,
  type Ruling,
  type TemplateView,
  type TurnRejected,
} from "../api.ts";
import { Host } from "../Host.tsx";
import { Icon } from "../Icons.tsx";
import { store } from "../store.ts";
import { capitalize, formName, lastStanding, rulingLine, standingBefore, truncate } from "./format.ts";
import { MatchEnd } from "./MatchEnd.tsx";
import { VerdictSheet } from "./Verdict.tsx";

type Props = { matchId: string };

export function Duel({ matchId }: Props) {
  const [snap, setSnap] = useState<MatchSnapshot | null>(null);
  const [template, setTemplate] = useState<TemplateView | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [returned, setReturned] = useState<TurnRejected | null>(null);
  const [streaming, setStreaming] = useState("");
  const [paused, setPaused] = useState<JudgePaused | null>(null);
  const [verdict, setVerdict] = useState<Ruling | null>(null);
  const [ended, setEnded] = useState<MatchEnded | null>(null);
  const [showResign, setShowResign] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => api.match(matchId).then(setSnap, (e) => setError(e.message)), [matchId]);

  useEffect(() => {
    void refresh();
    api.template().then(setTemplate, (e) => setError(e.message));
  }, [refresh]);

  const onEvent = useCallback(
    (event: MatchEvent) => {
      switch (event.name) {
        case "judge_started":
          setThinking(true);
          return;
        case "turn_rejected":
          setThinking(false);
          setPending(false);
          setReturned(event.data);
          setSnap((s) => s && { ...s, state_version: s.state_version + 1 });
          return;
        case "move_token":
          setStreaming((s) => s + event.data.text);
          return;
        case "judge_paused":
          setPaused(event.data);
          return;
        case "judge_resumed":
          setPaused(null);
          return;
        case "ruling": {
          const r = event.data;
          setThinking(false);
          setPending(false);
          setStreaming("");
          setReturned(null);
          if (r.actor === "p1") setText("");
          setSnap((s) =>
            s && {
              ...s,
              state_version: r.state_version,
              to_move: r.to_move,
              points_p1: r.points_p1,
              points_p2: r.points_p2,
              judged_moves: s.judged_moves + 1,
              transcript: s.transcript.some((t) => t.seq === r.seq)
                ? s.transcript
                : [
                    ...s.transcript,
                    { seq: r.seq, actor: r.actor, move_text: r.move_text, outcome: r.outcome, scoring: r.scoring, host: r.host },
                  ],
            },
          );
          setVerdict(r);
          return;
        }
        case "match_ended":
          setEnded(event.data);
          void refresh();
          return;
        case "state_resync":
          void refresh();
      }
    },
    [refresh],
  );
  useMatchEvents(matchId, onEvent);

  if (error) return <p className="page-status">{error}</p>;
  if (!snap || !template) return <p className="page-status">Finding your seat.</p>;

  if (ended && snap.status === "ended" && !verdict) {
    return <MatchEnd snap={snap} ended={ended} template={template} />;
  }

  const standing = lastStanding(snap.transcript);
  const standingText = streaming || standing?.move_text || snap.seed_token;
  const standingIsMine = !streaming && standing?.actor === "p1";
  const canPlay =
    snap.status === "active" && snap.to_move === "p1" && !pending && !paused && !ended;
  const round = snap.transcript.length + 1;
  const strikesNudge = returned?.nudge_text;

  async function play(event: FormEvent) {
    event.preventDefault();
    if (!canPlay || !text.trim()) return;
    setPending(true);
    setReturned(null);
    try {
      await api.move(matchId, snap!.state_version, text);
    } catch (e) {
      setPending(false);
      setError(null);
      setReturned({ outcome: "deterministic_invalid", reason_text: (e as Error).message, strikes: 0, nudge_text: null });
      void refresh();
    }
  }

  async function resign() {
    setShowResign(false);
    try {
      await api.resign(matchId, snap!.state_version);
    } catch (e) {
      setReturned({ outcome: "deterministic_invalid", reason_text: (e as Error).message, strikes: 0, nudge_text: null });
    }
  }

  const standingHead = streaming
    ? `${snap.opponent_name} is becoming`
    : standingIsMine
      ? "You became"
      : standing
        ? `Beat this, from ${snap.opponent_name}`
        : "Beat this, the opening";
  const composerLabel = paused
    ? "Draft your next move while you wait"
    : returned
      ? "Your move, still yours"
      : `Your move, beat ${formName(standingText)}`;
  const hostLine = paused
    ? paused.host_text
    : strikesNudge
      ? strikesNudge
      : thinking
        ? "The judge is reading."
        : standingIsMine
          ? `${snap.opponent_name} is thinking.`
          : `${capitalize(formName(standingText))}. Beat it, do not become it.`;

  return (
    <>
      <header className="bar-top">
        <a className="wordmark" href="/">
          Oddstage
        </a>
        <span className="round">Round {round}</span>
        <ThemeToggle />
      </header>

      <main className="stage">
        <section>
          <div className="scoreline">
            <span className="small-caps">You</span>
            <b>
              {snap.points_p1} : {snap.points_p2}
            </b>
            <span className="small-caps">{snap.opponent_name}</span>
          </div>
          <p className="centered-label small-caps" style={{ marginTop: "var(--space-3)" }}>
            The match so far
          </p>
          <ol className="transcript">
            <li>
              <div className="move">
                <span>
                  <b>R1:</b> {snap.seed_token}
                </span>
                <span className="medallion sm" role="img" aria-label={snap.seed_token}>
                  {snap.seed_emoji}
                </span>
              </div>
              <p className="ruling">The opening</p>
            </li>
            {snap.transcript.map((turn) => (
              <li key={turn.seq}>
                <div className="move">
                  <span>
                    <b>R{turn.seq + 1}:</b> {truncate(turn.move_text)}
                  </span>
                  <span className="medallion sm" role="img" aria-label={formName(turn.move_text)}>
                    {turn.host?.generated_emoji ?? "?"}
                  </span>
                </div>
                <p className="ruling">{rulingLine(turn)}</p>
              </li>
            ))}
            {paused && (
              <li>
                <div className="move">
                  <span>
                    <b>R{round}:</b> {truncate(text)}
                  </span>
                  <span className="medallion sm empty">?</span>
                </div>
                <p className="ruling">Waiting for the judge</p>
              </li>
            )}
          </ol>
          <p className="resign-row">
            <button className="quiet-button" type="button" onClick={() => setShowResign(true)} disabled={!!ended}>
              <Icon name="flag" />
              Resign the match
            </button>
          </p>
        </section>

        <section>
          <p className="small-caps last-move-head">{standingHead}</p>
          <div className="torn">
            {paused && <span className="tag">Awaiting ruling</span>}
            <p className="last-move">{paused ? text : standingText}</p>
          </div>
          {paused && <p className="waiting">The match is paused until the judge rules.</p>}
          {thinking && !paused && <p className="waiting">The judge is considering.</p>}

          <form className={`composer${returned ? " returned" : ""}`} style={{ marginTop: "var(--space-3)" }} onSubmit={play}>
            {returned && (
              <span className="slip returned-slip" aria-hidden="true">
                Returned, try again
              </span>
            )}
            <label className="small-caps" htmlFor="move">
              {composerLabel}
            </label>
            <textarea
              id="move"
              maxLength={template.max_chars}
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={pending || !!ended || (snap.to_move !== "p1" && !paused)}
              placeholder={
                paused
                  ? "Nothing to lose by thinking ahead. It sends when play resumes."
                  : `Become something ${formName(standingText)} cannot survive. ${template.max_chars} characters.`
              }
            />
            {returned && (
              <p className="why">
                <b>Not a move yet.</b> {returned.reason_text}
              </p>
            )}
            <div className="composer-foot">
              <span className="counter" aria-live="polite">
                {text.length}/{template.max_chars}
              </span>
              <button className={`ticket${canPlay ? "" : " quiet"}`} type="submit" disabled={!canPlay}>
                {pending ? "Sent" : "Play it"}
              </button>
            </div>
          </form>
        </section>

        <section>
          <p className="centered-label small-caps">The rubric</p>
          <div className="rubric">
            {template.rubric.map((entry) => (
              <details key={entry.name} open={!store.firstPlayDone() || snap.transcript.length < 2}>
                <summary>{entry.name.replace(/_/g, " ")}</summary>
                <p>{entry.description}</p>
              </details>
            ))}
          </div>
          <div className="host" style={{ marginTop: "var(--space-3)" }}>
            <Host state={paused || thinking ? "thinking" : "idle"} />
            <p className="host-line">{hostLine}</p>
          </div>
        </section>
      </main>

      {verdict && (
        <VerdictSheet
          ruling={verdict}
          previous={standingBefore(snap.transcript, verdict.seq, snap.seed_token)}
          opponentName={snap.opponent_name}
          template={template}
          ended={!!ended}
          onNext={() => setVerdict(null)}
          onDisagree={() => api.disagree(matchId, verdict.seq)}
        />
      )}

      {showResign && (
        <div className="scrim">
          <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="give-up-title" style={{ maxWidth: "26rem" }}>
            <h2 id="give-up-title" style={{ margin: 0, fontSize: "1.8rem" }}>
              Resign this match?
            </h2>
            <p style={{ margin: "var(--space-2) 0 0", color: "var(--ink-soft)" }}>
              {snap.opponent_name} takes the win at {snap.points_p1} : {snap.points_p2} after{" "}
              {snap.judged_moves} judged moves. The replay is saved either way.
            </p>
            <div className="sheet-actions">
              <button className="quiet-button" type="button" onClick={resign}>
                <Icon name="flag" />
                Resign
              </button>
              <button className="ticket" type="button" onClick={() => setShowResign(false)} autoFocus>
                Keep playing
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
