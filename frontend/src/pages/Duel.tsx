import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { ThemeToggle } from "../App.tsx";
import {
  api,
  useMatchEvents,
  type JudgePaused,
  type MatchEnded,
  type MatchEvent,
  type MatchSnapshot,
  type Replay,
  type TemplateView,
  type TurnRejected,
  type TurnView,
} from "../api.ts";
import { Host } from "../Host.tsx";
import { Icon } from "../Icons.tsx";
import { store } from "../store.ts";
import { capitalize, criterionLabel, formName, fullMove, HOUSE, lastStanding } from "./format.ts";
import { MatchEnd } from "./MatchEnd.tsx";

function endedFromReplay(r: Replay): MatchEnded {
  return {
    end_reason: r.end_reason!,
    winner: r.winner,
    points_p1: r.points_p1,
    points_p2: r.points_p2,
    highlight_seq: r.highlight_seq,
    coaching_line: null,
    share_text: r.share_text,
    replay_id: r.id,
    state_version: r.state_version,
  };
}
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
  const [ended, setEnded] = useState<MatchEnded | null>(null);
  const [showResign, setShowResign] = useState(false);
  const [showRubric, setShowRubric] = useState(false);
  const [voted, setVoted] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const transcriptRef = useRef<HTMLOListElement>(null);

  const refresh = useCallback(
    () =>
      api.match(matchId).then((s) => {
        setSnap(s);
        const opening = store.takeOpeningMove(matchId);
        if (opening && s.status === "awaiting_judgment") {
          setText(opening);
          setPending(true);
        }
        if (s.status === "ended" && s.end_reason) return api.replay(matchId).then(endedFromReplay).then(setEnded);
      }, (e) => setError(e.message)),
    [matchId],
  );

  useEffect(() => {
    void refresh();
    api.template().then(setTemplate, (e) => setError(e.message));
  }, [refresh]);

  const transcriptLength = snap?.transcript.length ?? 0;
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [transcriptLength]);

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
                    {
                      seq: r.seq,
                      actor: r.actor,
                      move_text: r.move_text,
                      outcome: r.outcome,
                      scoring: r.scoring,
                      host: r.host,
                      points: r.points,
                    },
                  ],
            },
          );
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

  if (ended && snap.status === "ended") {
    return <MatchEnd snap={snap} ended={ended} template={template} />;
  }

  const prefix = template.move_prefix;
  const standing = lastStanding(snap.transcript);
  const latest = snap.transcript[snap.transcript.length - 1];
  const yourLast = [...snap.transcript].reverse().find((t) => t.actor === "p1" && t.host);
  const showYourSlip = yourLast && latest?.actor === "p2" && !streaming;
  const standingText = streaming || standing?.move_text || snap.seed_token;
  const standingIsMine = !streaming && standing?.actor === "p1";
  const waiting = pending || thinking || !!streaming || (snap.to_move === "p2" && !paused);
  const canPlay = snap.status === "active" && snap.to_move === "p1" && !pending && !paused && !ended;
  const round = snap.transcript.length + 1;
  const strikesNudge = returned?.nudge_text;
  const totalAvailable = template.rubric.reduce((sum, r) => sum + r.max_points, 0);

  async function play(event: FormEvent) {
    event.preventDefault();
    if (!canPlay || !text.trim()) return;
    setPending(true);
    setReturned(null);
    try {
      await api.move(matchId, snap!.state_version, fullMove(prefix, text));
    } catch (e) {
      setPending(false);
      setError(null);
      setReturned({ outcome: "deterministic_invalid", reason_text: (e as Error).message, strikes: 0, nudge_text: null });
      void refresh();
    }
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    formRef.current?.requestSubmit();
  }

  async function resign() {
    setShowResign(false);
    try {
      await api.resign(matchId, snap!.state_version);
    } catch (e) {
      setReturned({ outcome: "deterministic_invalid", reason_text: (e as Error).message, strikes: 0, nudge_text: null });
    }
  }

  function disagree(seq: number) {
    void api.disagree(matchId, seq).then(() => setVoted((v) => new Set(v).add(seq)));
  }

  const standingHead = streaming
    ? `${HOUSE} is becoming`
    : standingIsMine
      ? "You became"
      : standing
        ? `Beat this, from ${HOUSE}`
        : "Beat this, the opening";
  const composerLabel = paused
    ? "Draft your next move while you wait"
    : returned
      ? "Your move, still yours"
      : `Your move, beat ${formName(standingText, prefix)}`;
  const hostLine = paused
    ? paused.host_text
    : strikesNudge
      ? strikesNudge
      : waiting
        ? standingIsMine
          ? `${HOUSE} is thinking.`
          : "The judge is reading."
        : `${capitalize(formName(standingText, prefix))}. Beat it, do not become it.`;

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
            <span className="small-caps">{HOUSE}</span>
          </div>
          <p className="centered-label small-caps" style={{ marginTop: "var(--space-3)" }}>
            The match so far
          </p>
          <ol className="transcript">
            <li>
              <div className="move">
                <span>
                  <span className="who">The opening</span>
                  {snap.seed_token}
                </span>
                <span className="medallion sm" role="img" aria-label={snap.seed_token}>
                  {snap.seed_emoji}
                </span>
              </div>
            </li>
            {snap.transcript.map((turn) => (
              <li key={turn.seq}>
                <div className="move">
                  <span>
                    <span className={`who${turn.actor === "p1" ? " you" : ""}`}>{turn.actor === "p1" ? "You" : HOUSE}</span>
                    {turn.move_text}
                  </span>
                  <span className="medallion sm" role="img" aria-label={formName(turn.move_text, prefix)}>
                    {turn.host?.generated_emoji ?? "?"}
                  </span>
                </div>
                <p className="ruling">
                  <span className="word">{rulingWord(turn)}</span>
                  <em>{turn.host?.headline}</em>
                </p>
              </li>
            ))}
            {paused && (
              <li>
                <div className="move">
                  <span>
                    <span className="who you">You</span>
                    {fullMove(prefix, text)}
                  </span>
                  <span className="medallion sm empty">?</span>
                </div>
                <p className="ruling">
                  <em>Waiting for the judge</em>
                </p>
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
          {showYourSlip && (
            <p className="your-slip">
              <span className="medallion" aria-hidden="true">
                {yourLast.host?.generated_emoji}
              </span>
              <span>
                <b>
                  {rulingWord(yourLast)} for {formName(yourLast.move_text, prefix)}.
                </b>{" "}
                <q>{yourLast.host?.headline}</q>
              </span>
            </p>
          )}

          <p className="small-caps last-move-head">{standingHead}</p>
          <div className={`torn standing${waiting ? " reading" : ""}`}>
            {paused && <span className="tag">Awaiting ruling</span>}
            {standing && !streaming && !paused && (
              <span className={`stamp corner${standing.outcome === "semantic_uncertain" ? " ink" : ""}`}>
                {standing.outcome === "semantic_uncertain" ? "Close call" : `Point: ${formName(standing.move_text, prefix)}`}
              </span>
            )}
            <p className="last-move">{paused ? fullMove(prefix, text) : standingText}</p>
            {standing?.host && !streaming && !paused && (
              <div className="ruled">
                <Host state="verdict" />
                <div>
                  <p className="headline">{standing.host.headline}</p>
                  <p className="because">
                    {criterionLabel(standing.host.because_clause.criterion)}: {standing.host.because_clause.text}
                  </p>
                  <button
                    className="vote"
                    type="button"
                    disabled={voted.has(standing.seq)}
                    onClick={() => disagree(standing.seq)}
                  >
                    <Icon name="speech" />
                    {voted.has(standing.seq) ? "Noted. The ruling stands." : "I disagree with this ruling"}
                  </button>
                </div>
              </div>
            )}
          </div>
          {paused && <p className="waiting">The match is paused until the judge rules.</p>}

          <form
            ref={formRef}
            className={`composer${returned ? " returned" : ""}`}
            style={{ marginTop: "var(--space-3)" }}
            onSubmit={play}
          >
            {returned && (
              <span className="slip returned-slip" aria-hidden="true">
                Returned, try again
              </span>
            )}
            <label className="small-caps" htmlFor="move">
              {composerLabel}
            </label>
            <div className="compose-box">
              <span className="prefix" aria-hidden="true">
                {prefix}
              </span>
              <textarea
                id="move"
                autoComplete="off"
                data-form-type="other"
                data-lpignore="true"
                data-1p-ignore=""
                maxLength={template.max_chars - prefix.length}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={onKey}
                disabled={pending || !!ended || (snap.to_move !== "p1" && !paused)}
                placeholder={paused ? "thinking ahead. It sends when play resumes." : template.move_example.slice(prefix.length)}
              />
            </div>
            <p className="compose-hint">Enter sends. Shift+Enter for a new line.</p>
            {returned && (
              <p className="why">
                <b>Not a move yet.</b> {returned.reason_text}
              </p>
            )}
            <div className="composer-foot">
              <span className="counter" aria-live="polite">
                {fullMove(prefix, text).length}/{template.max_chars}
              </span>
              <button className={`ticket${canPlay ? "" : " quiet"}`} type="submit" disabled={!canPlay}>
                {pending ? "Sent" : "Play it"}
              </button>
            </div>
          </form>

          {waiting && !paused && (
            <div className="judge-reading" role="status">
              <Host state="thinking" />
              <span>
                {streaming ? `${HOUSE} is answering` : "The judge is reading"}
                <span className="dots" />
              </span>
            </div>
          )}
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
                  <span>{latest.actor === "p1" ? "Your move" : `${HOUSE}'s move`}</span>
                  <span>The judge was {latest.scoring.confidence}</span>
                </p>
                <dl className="points">
                  {template.rubric.map((entry) => {
                    const earned = (latest.scoring?.scores[entry.name] ?? 0) * (entry.max_points / template.score_max);
                    const decided = entry.name === latest.host?.because_clause.criterion;
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
                <p className="decided-note">
                  <b>Red</b> marks the criterion that decided the ruling.
                </p>
                <p className="total">
                  <span>This move</span>
                  <b>
                    {latest.points ?? 0} of {totalAvailable}
                  </b>
                </p>
              </div>
            </>
          ) : (
            <>
              <p className="panel-head">
                <span className="small-caps">Scoring rules</span>
                {latest?.scoring && (
                  <button className="swap small-caps" type="button" onClick={() => setShowRubric(false)}>
                    Last ruling
                  </button>
                )}
              </p>
              <div className="rubric">
                {template.rubric.map((entry) => (
                  <details key={entry.name} open>
                    <summary>
                      {criterionLabel(entry.name)} <small>up to {entry.max_points}</small>
                    </summary>
                    <p>{entry.description}</p>
                  </details>
                ))}
              </div>
            </>
          )}
          <div className="host" style={{ marginTop: "var(--space-3)" }}>
            <Host state={paused || waiting ? "thinking" : "idle"} />
            <p className="host-line">{hostLine}</p>
          </div>
        </section>
      </main>

      {showResign && (
        <div className="scrim">
          <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="give-up-title" style={{ maxWidth: "26rem" }}>
            <h2 id="give-up-title" style={{ margin: 0, fontSize: "1.8rem" }}>
              Resign this match?
            </h2>
            <p style={{ margin: "var(--space-2) 0 0", color: "var(--ink-soft)" }}>
              {HOUSE} takes the win at {snap.points_p1} : {snap.points_p2} after {snap.judged_moves} judged moves. The
              replay is saved either way.
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

function rulingWord(turn: TurnView): string {
  if (turn.outcome === "fail") return "Fell";
  if (turn.outcome === "semantic_uncertain") return `Close call, ${turn.points ?? 0}`;
  return `Point, ${turn.points ?? 0}`;
}
