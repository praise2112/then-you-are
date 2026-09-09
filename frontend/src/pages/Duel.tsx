import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type RefObject } from "react";

import { navigate, ThemeToggle } from "../App.tsx";
import {
  api,
  useMatchEvents,
  type JudgePaused,
  type MatchEnded,
  type MatchEvent,
  type MatchSnapshot,
  type Replay,
  type Ruling,
  type TemplateView,
  type TurnRejected,
  type TurnView,
} from "../api.ts";
import { Host } from "../Host.tsx";
import { Icon } from "../Icons.tsx";
import { store } from "../store.ts";
import { capitalize, criterionLabel, formName, fullMove, groupRounds, HOUSE, lastStanding } from "./format.ts";
import { MatchEnd } from "./MatchEnd.tsx";
import { Bluff, roundWinner, TruthLine, WordCard } from "./rounds.tsx";

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

function turnOf(r: Ruling): TurnView {
  return {
    seq: r.seq,
    round_n: r.round_n,
    actor: r.actor,
    move_text: r.move_text,
    outcome: r.outcome,
    scoring: r.scoring,
    host: r.host,
    points: r.points,
  };
}

function withRulings(s: MatchSnapshot, rulings: Ruling[]): MatchSnapshot {
  const fresh = rulings.filter((r) => !s.transcript.some((t) => t.seq === r.seq));
  const last = rulings[rulings.length - 1];
  if (!last) return s;
  return {
    ...s,
    state_version: last.state_version,
    to_move: last.to_move,
    points_p1: last.points_p1,
    points_p2: last.points_p2,
    judged_moves: s.judged_moves + fresh.length,
    transcript: [...s.transcript, ...fresh.map(turnOf)],
  };
}

type Props = { matchId: string; spectator?: boolean };

export function Duel({ matchId, spectator = false }: Props) {
  const [snap, setSnap] = useState<MatchSnapshot | null>(null);
  const [template, setTemplate] = useState<TemplateView | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [returned, setReturned] = useState<TurnRejected | null>(null);
  const [streaming, setStreaming] = useState("");
  const [paused, setPaused] = useState<JudgePaused | null>(null);
  const [ended, setEnded] = useState<MatchEnded | null>(null);
  const [revealEnd, setRevealEnd] = useState(false);
  const [showResign, setShowResign] = useState(false);
  const [showRubric, setShowRubric] = useState(false);
  const [voted, setVoted] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const transcriptRef = useRef<HTMLOListElement>(null);
  const modeRef = useRef<MatchSnapshot["mode"]>("escalation");
  const heldRef = useRef<Ruling[]>([]);

  const refresh = useCallback(
    () =>
      api.match(matchId).then((s) => {
        setSnap(s);
        modeRef.current = s.mode;
        const opening = store.takeOpeningMove(matchId);
        if (opening && s.status === "awaiting_judgment") {
          setText(opening);
          setPending(true);
        }
        if (s.status === "ended" && spectator) return navigate(`/r/${matchId}`);
        if (s.status === "ended" && s.end_reason) {
          setRevealEnd(true);
          return api.replay(matchId).then(endedFromReplay).then(setEnded);
        }
      }, (e) => setError(e.message)),
    [matchId, spectator],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const templateId = snap?.template_id;
  useEffect(() => {
    if (templateId) api.template(templateId).then(setTemplate, (e) => setError(e.message));
  }, [templateId]);

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
          if (modeRef.current === "showcase") {
            heldRef.current.push(r);
            return;
          }
          setThinking(false);
          setPending(false);
          setStreaming("");
          setReturned(null);
          if (r.actor === "p1") setText("");
          setSnap((s) => s && withRulings(s, [r]));
          return;
        }
        case "round_revealed": {
          const held = heldRef.current;
          heldRef.current = [];
          const r = event.data;
          setThinking(false);
          setPending(false);
          setReturned(null);
          setText("");
          setSnap(
            (s) =>
              s && {
                ...withRulings(s, held),
                state_version: r.state_version,
                rounds: s.rounds.map((x) => (x.round_n === r.round_n ? { ...x, truth: r.truth, emoji: r.emoji } : x)),
              },
          );
          void api.match(matchId).then((s) => setSnap((prev) => prev && { ...prev, rounds: s.rounds }));
          return;
        }
        case "match_ended": {
          const e = event.data;
          setThinking(false);
          setStreaming("");
          setSnap((s) => s && { ...s, status: "ended", winner: e.winner, end_reason: e.end_reason, points_p1: e.points_p1, points_p2: e.points_p2 });
          setEnded(e);
          setTimeout(() => (spectator ? navigate(`/r/${matchId}`) : setRevealEnd(true)), 3200);
          return;
        }
        case "state_resync":
          void refresh();
      }
    },
    [refresh, spectator, matchId],
  );
  useMatchEvents(matchId, onEvent);

  if (error) return <p className="page-status">{error}</p>;
  if (!snap || !template) return <p className="page-status">Finding your seat.</p>;

  if (ended && revealEnd && snap.status === "ended") {
    return <MatchEnd snap={snap} ended={ended} template={template} />;
  }

  const prefix = template.move_prefix;
  const me = spectator ? snap.stage_name : "You";
  const standing = lastStanding(snap.transcript);
  const latest = snap.transcript[snap.transcript.length - 1];
  const fell = ended && latest?.outcome === "fail" ? latest : null;
  const shown = fell ?? standing;
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

  if (snap.mode === "showcase") {
    return (
      <ShowcaseDuel
        snap={snap}
        template={template}
        spectator={spectator}
        text={text}
        setText={setText}
        pending={pending}
        thinking={thinking}
        returned={returned}
        paused={paused}
        ended={!!ended}
        play={play}
        resign={resign}
        formRef={formRef}
      />
    );
  }

  const standingHead = fell
    ? fell.actor === "p1"
      ? `${me} fell`
      : `${HOUSE} fell`
    : streaming
    ? `${HOUSE} is becoming`
    : standingIsMine
      ? `${me} became`
      : standing
        ? `Beat this, from ${HOUSE}`
        : "Beat this, the opening";
  const composerLabel = paused
    ? "Draft your next move while you wait"
    : returned
      ? "Your move, still yours"
      : `Your move, beat ${formName(standingText, prefix)}`;
  const hostLine = fell
    ? "The match is over. One moment."
    : paused
    ? paused.host_text
    : strikesNudge
      ? strikesNudge
      : waiting
        ? standingIsMine
          ? `${HOUSE} is thinking.`
          : "The judge is reading."
        : spectator
          ? `${capitalize(formName(standingText, prefix))} stands. ${snap.stage_name} to move.`
          : `${capitalize(formName(standingText, prefix))}. Beat it, do not become it.`;

  return (
    <>
      <header className="bar-top">
        <a className="wordmark" href="/">
          Oddstage
        </a>
        <span className="round">
          {spectator && <b>Watching </b>}Round {round}
        </span>
        <ThemeToggle />
      </header>

      <main className="stage">
        <section>
          <div className="scoreline">
            <span className="small-caps">{me}</span>
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
                    <span className={`who${turn.actor === "p1" ? " you" : ""}`}>{turn.actor === "p1" ? me : HOUSE}</span>
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
          {!spectator && (
            <p className="resign-row">
              <button className="quiet-button" type="button" onClick={() => setShowResign(true)} disabled={!!ended}>
                <Icon name="flag" />
                Resign the match
              </button>
            </p>
          )}
        </section>

        <section>
          {showYourSlip && !spectator && (
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
            {shown && !streaming && !paused && (
              <span className={`stamp corner${shown.outcome === "accept" ? "" : " ink"}`}>
                {fell ? `Fell: ${formName(fell.move_text, prefix)}` : shown.outcome === "semantic_uncertain" ? "Close call" : `Point: ${formName(shown.move_text, prefix)}`}
              </span>
            )}
            <p className="last-move">{paused ? fullMove(prefix, text) : fell ? fell.move_text : standingText}</p>
            {shown?.host && !streaming && !paused && (
              <div className="ruled">
                <Host state="verdict" />
                <div>
                  <p className="headline">{shown.host.headline}</p>
                  <p className="because">
                    {criterionLabel(shown.host.because_clause.criterion)}: {shown.host.because_clause.text}
                  </p>
                  <button
                    className="vote"
                    type="button"
                    disabled={voted.has(shown.seq)}
                    onClick={() => disagree(shown.seq)}
                  >
                    <Icon name="speech" />
                    {voted.has(shown.seq) ? "Noted. The ruling stands." : "I disagree with this ruling"}
                  </button>
                </div>
              </div>
            )}
          </div>
          {paused && <p className="waiting">The match is paused until the judge rules.</p>}

          {!spectator && (
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
          )}
          {spectator && !waiting && !paused && (
            <p className="waiting">Waiting for {snap.stage_name} to move.</p>
          )}

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
                  <span>{latest.actor === "p1" ? (spectator ? `${me}'s move` : "Your move") : `${HOUSE}'s move`}</span>
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

type ShowcaseProps = {
  snap: MatchSnapshot;
  template: TemplateView;
  spectator: boolean;
  text: string;
  setText: (text: string) => void;
  pending: boolean;
  thinking: boolean;
  returned: TurnRejected | null;
  paused: JudgePaused | null;
  ended: boolean;
  play: (event: FormEvent) => Promise<void>;
  resign: () => Promise<void>;
  formRef: RefObject<HTMLFormElement | null>;
};

/** The round view: one card at a time, both answers shown together once the truth is out. */
function ShowcaseDuel({ snap, template, spectator, text, setText, pending, thinking, returned, paused, ended, play, resign, formRef }: ShowcaseProps) {
  const [showResign, setShowResign] = useState(false);
  const me = spectator ? snap.stage_name : "You";
  const groups = groupRounds(snap.rounds, snap.transcript);
  const revealed = groups.filter((g) => g.revealed);
  const lastResult = revealed[revealed.length - 1];
  const earlier = revealed.slice(0, -1);
  const current = groups.find((g) => !g.revealed);
  const roundN = current?.round.round_n ?? lastResult?.round.round_n ?? 1;
  const judging = pending || thinking || snap.status === "awaiting_judgment";
  const canPlay = snap.status === "active" && !pending && !paused && !ended && !!current;
  const hostLine = ended
    ? "The match is over. One moment."
    : paused
      ? paused.host_text
      : returned?.nudge_text
        ? returned.nudge_text
        : judging
          ? "The Judge is reading both."
          : spectator
            ? `${snap.stage_name} is writing.`
            : template.move_hint;

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    formRef.current?.requestSubmit();
  }

  return (
    <>
      <header className="bar-top">
        <a className="wordmark" href="/">
          Oddstage
        </a>
        <span className="round">
          {spectator && <b>Watching </b>}Round {roundN} of {template.rounds}
        </span>
        <ThemeToggle />
      </header>

      <main className="stage showcase">
        <section>
          <div className="scoreline">
            <span className="small-caps">{me}</span>
            <b>
              {snap.points_p1} : {snap.points_p2}
            </b>
            <span className="small-caps">{HOUSE}</span>
          </div>

          {earlier.length > 0 && (
            <ol className="rounds-so-far">
              {earlier.map(({ round, mine, theirs }) => (
                <li key={round.round_n}>
                  <span className="medallion sm" role="img" aria-label={round.token}>
                    {round.emoji}
                  </span>
                  <span>
                    <b>{round.token}</b> <em>{round.truth}</em>
                  </span>
                  <span className="tally-line">
                    {roundWinner(mine, theirs) === "mine" ? <b>{mine?.points ?? 0}</b> : mine?.points ?? 0}
                    {" : "}
                    {roundWinner(mine, theirs) === "theirs" ? <b>{theirs?.points ?? 0}</b> : theirs?.points ?? 0}
                  </span>
                </li>
              ))}
            </ol>
          )}

          {lastResult && (
            <section className="round-result">
              <TruthLine round={lastResult.round} />
              <div className="bluffs">
                <Bluff
                  turn={lastResult.mine!}
                  who={me}
                  you
                  won={roundWinner(lastResult.mine, lastResult.theirs) === "mine"}
                  template={template}
                />
                <Bluff
                  turn={lastResult.theirs!}
                  who={HOUSE}
                  won={roundWinner(lastResult.mine, lastResult.theirs) === "theirs"}
                  template={template}
                />
              </div>
            </section>
          )}

          {current && (
            <>
              <p className="small-caps last-move-head" style={{ marginTop: lastResult ? "var(--space-3)" : "var(--space-2)" }}>
                {lastResult ? "The next word" : "The word"}
              </p>
              <WordCard round={current.round} compact={!!lastResult}>
                {judging && <span className="tag">Being judged</span>}
              </WordCard>
            </>
          )}

          {!spectator && current && (
            <form ref={formRef} className={`composer${returned ? " returned" : ""}`} style={{ marginTop: "var(--space-3)" }} onSubmit={play}>
              {returned && (
                <span className="slip returned-slip" aria-hidden="true">
                  Returned, try again
                </span>
              )}
              <label className="small-caps" htmlFor="move">
                {paused ? "Draft your definition while you wait" : `Your definition of ${current.round.token}`}
              </label>
              <div className={`compose-box bare${judging && !paused ? " scanning" : ""}`}>
                <textarea
                  id="move"
                  autoComplete="off"
                  data-form-type="other"
                  data-lpignore="true"
                  data-1p-ignore=""
                  maxLength={template.max_chars}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={onKey}
                  disabled={pending || ended || judging}
                  placeholder={paused ? "thinking ahead. It sends when play resumes." : template.move_example}
                />
              </div>
              <p className="compose-hint">Enter sends. Shift+Enter for a new line.</p>
              {returned && (
                <p className="why">
                  <b>Not a definition yet.</b> {returned.reason_text}
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
          )}
          {paused && <p className="waiting">The match is paused until the judge rules.</p>}
          {spectator && current && !judging && <p className="waiting">Waiting for {snap.stage_name} to write.</p>}

          {judging && !paused && (
            <div className="judge-reading" role="status">
              <Host state="thinking" />
              <span>
                The Judge is reading both
                <span className="dots" />
              </span>
            </div>
          )}

          {!spectator && (
            <p className="resign-row">
              <button className="quiet-button" type="button" onClick={() => setShowResign(true)} disabled={ended}>
                <Icon name="flag" />
                Resign the match
              </button>
            </p>
          )}
        </section>

        <section>
          <p className="panel-head">
            <span className="small-caps">Scoring rules</span>
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
          <p className="rules-text">{template.rules_text}</p>
          <div className="host" style={{ marginTop: "var(--space-3)" }}>
            <Host state={paused || judging ? "thinking" : "idle"} />
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
              {HOUSE} takes the win at {snap.points_p1} : {snap.points_p2} after {revealed.length} of {template.rounds} rounds. The
              replay is saved either way.
            </p>
            <div className="sheet-actions">
              <button
                className="quiet-button"
                type="button"
                onClick={() => {
                  setShowResign(false);
                  void resign();
                }}
              >
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
