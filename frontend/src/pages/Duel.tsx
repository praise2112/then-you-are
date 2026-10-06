import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type RefObject } from "react";

import { Link, navigate, ThemeToggle, TopBar } from "../App.tsx";
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
import {
  capitalize,
  clockText,
  criterionLabel,
  endLine,
  formName,
  fullMove,
  groupRounds,
  isLive,
  isLongCard,
  judgeFace,
  lastStanding,
  roundTotals,
  roundWinner,
  shortName,
  tableOf,
  useSecondsLeft,
  type Table,
} from "./format.ts";
import { ResultCard } from "./ResultCard.tsx";
import { Bluff, CallCard, CallLine, RoundLedger, TruthLine, WordCard } from "./rounds.tsx";
import { SeatList, SeatStrip, WaitingRoom } from "./seats.tsx";

function endedFromReplay(r: Replay): MatchEnded {
  // Walking back into a finished match: the coaching line is on the move that fell.
  const last = r.transcript[r.transcript.length - 1];
  return {
    end_reason: r.end_reason!,
    winner: r.winner,
    totals: Object.fromEntries(r.seats.map((s) => [s.seat, s.points])),
    highlight_seq: r.highlight_seq,
    coaching_line: last?.outcome === "fail" ? (last.host?.coaching_line ?? null) : null,
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

/** A short form fits on the stamp; a sentence-length move leaves the verb alone. */
function stampText(verb: string, move: string, prefix: string): string {
  const name = formName(move, prefix);
  return name.length > 28 ? verb : `${verb}: ${name}`;
}

function withTotals(s: MatchSnapshot, totals: Record<string, number>): MatchSnapshot["seats"] {
  return s.seats.map((seat) => ({ ...seat, points: totals[seat.seat] ?? seat.points }));
}

function withRuling(s: MatchSnapshot, r: Ruling): MatchSnapshot {
  return {
    ...s,
    status: "active",
    state_version: r.state_version,
    to_move: r.to_move,
    round_in_play: r.round_in_play,
    seats: withTotals(s, r.totals),
    judged_moves: s.judged_moves + 1,
    transcript: [...s.transcript, turnOf(r)],
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
  const [picked, setPicked] = useState<string | null>(null);
  const [calling, setCalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [after, setAfter] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const transcriptRef = useRef<HTMLOListElement>(null);
  const modeRef = useRef<MatchSnapshot["mode"]>("escalation");
  const seatRef = useRef<string | null>(null);
  const prefixRef = useRef("");
  // The newest state version the client holds or has a fetch under way for.
  const versionRef = useRef(-1);
  const fetchingRef = useRef(false);
  const behindRef = useRef(false);

  const refresh = useCallback(() => {
    if (fetchingRef.current) {
      behindRef.current = true;
      return;
    }
    fetchingRef.current = true;
    api
      .match(matchId)
      .then((s) => {
        if (s.state_version < versionRef.current) return;
        versionRef.current = s.state_version;
        setSnap(s);
        if (s.status === "open" || isLive(s.status)) setAfter((a) => a ?? s.event_id);
        modeRef.current = s.mode;
        seatRef.current = spectator ? null : s.your_seat;
        // A showcase refusal says why only in the refused seat's own snapshot.
        if (s.mode === "showcase" && s.returned) {
          setReturned(s.returned);
          setPending(false);
        }
        const opening = store.takeOpeningMove(matchId);
        if (opening) {
          setText(opening);
          if (s.status === "awaiting_judgment") setPending(true);
        }
        if (s.status === "ended" && spectator) return navigate(`/r/${matchId}`);
        if (s.status === "ended" && s.end_reason) {
          setRevealEnd(true);
          return api.replay(matchId).then(endedFromReplay).then(setEnded);
        }
      }, (e) => setError(e.message))
      .finally(() => {
        fetchingRef.current = false;
        if (!behindRef.current) return;
        behindRef.current = false;
        refresh();
      });
  }, [matchId, spectator]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const templateId = snap?.template_id;
  useEffect(() => {
    if (!templateId) return;
    api.template(templateId).then(
      (t) => {
        prefixRef.current = t.move_prefix;
        setTemplate(t);
      },
      (e) => setError(e.message),
    );
  }, [templateId]);

  const transcriptLength = snap?.transcript.length ?? 0;
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [transcriptLength]);

  const onEvent = useCallback(
    (event: MatchEvent) => {
      // Fetches the snapshot when the event's version is newer than the client's.
      const catchUp = (version: number) => {
        if (version <= versionRef.current) return;
        versionRef.current = version;
        refresh();
      };
      // Applies a change the event fully describes, unless the client lacks one before it.
      const advance = (version: number, update: (s: MatchSnapshot) => MatchSnapshot) => {
        if (version !== versionRef.current + 1 || fetchingRef.current) return catchUp(version);
        versionRef.current = version;
        setSnap((s) => s && update(s));
      };
      // A change the version does not count; a fetch already under way may predate it.
      const patch = (update: (s: MatchSnapshot) => MatchSnapshot) => {
        setSnap((s) => s && update(s));
        if (fetchingRef.current) behindRef.current = true;
      };
      switch (event.name) {
        case "judge_started":
          setThinking(true);
          return;
        case "turn_rejected": {
          const r = event.data;
          const mine = r.seat === seatRef.current;
          if (mine) {
            setThinking(false);
            setPending(false);
          }
          // A showcase refusal says why only in the refused seat's own snapshot.
          if (modeRef.current === "showcase") {
            if (mine) catchUp(r.state_version);
            return;
          }
          if (mine) setReturned(r);
          advance(r.state_version, (s) => ({ ...s, status: "active", state_version: r.state_version }));
          return;
        }
        case "move_token":
          setStreaming((s) => s + event.data.text);
          return;
        case "judge_paused":
          setPaused(event.data);
          setText((t) => t || event.data.move_text.slice(prefixRef.current.length));
          return;
        case "judge_resumed":
          setPaused(null);
          return;
        case "ruling": {
          // A showcase round's rulings arrive together with its reveal, read from the snapshot.
          if (modeRef.current === "showcase") return;
          const r = event.data;
          if (r.state_version <= versionRef.current) return;
          setThinking(false);
          setStreaming("");
          if (r.actor === seatRef.current) {
            setPending(false);
            setReturned(null);
            setText("");
          }
          advance(r.state_version, (s) => withRuling(s, r));
          return;
        }
        case "round_revealed":
          setCalling(false);
          setPicked(null);
          setReturned(null);
          setPending(false);
          setText("");
          catchUp(event.data.state_version);
          return;
        case "guess_opened":
          setPending(false);
          setPicked(null);
          setCalling(false);
          catchUp(event.data.state_version);
          return;
        case "seat_submitted": {
          const { seat, state_version } = event.data;
          if (state_version > versionRef.current) return catchUp(state_version);
          patch((s) => ({ ...s, seats: s.seats.map((x) => (x.seat === seat ? { ...x, answered: true } : x)) }));
          return;
        }
        case "turn_changed": {
          const t = event.data;
          if (t.state_version > versionRef.current) return catchUp(t.state_version);
          patch((s) => ({
            ...s,
            status: isLive(s.status) ? "active" : s.status,
            to_move: t.to_move,
            turn_deadline: t.turn_deadline,
            round_in_play: t.round_in_play,
          }));
          return;
        }
        // Filling a table leaves the version at 0.
        case "seat_joined":
        case "match_started":
          refresh();
          return;
        case "match_ended": {
          const e = event.data;
          if (e.end_reason === "unfilled") {
            refresh();
            return;
          }
          setThinking(false);
          setStreaming("");
          setSnap((s) => s && { ...s, status: "ended", winner: e.winner, end_reason: e.end_reason, seats: withTotals(s, e.totals) });
          setEnded(e);
          setTimeout(() => (spectator ? navigate(`/r/${matchId}`) : setRevealEnd(true)), e.end_reason === "resign" ? 0 : 3200);
          if (modeRef.current === "showcase") catchUp(e.state_version);
          return;
        }
      }
    },
    [refresh, spectator, matchId],
  );
  useMatchEvents(matchId, after, onEvent, refresh);

  if (error) return <p className="page-status">{error}</p>;
  if (!snap || !template) return <p className="page-status">Finding your seat.</p>;
  if (snap.status === "abandoned") {
    return (
      <p className="page-status">
        {snap.end_reason === "unfilled" ? "Nobody joined this table in time." : "This match closed after a day without a move."}{" "}
        <Link to={`/play/${snap.template_id}`}>Start a fresh one</Link>.
      </p>
    );
  }

  const table = tableOf(snap, spectator);
  if (snap.status === "open") {
    return (
      <>
        <TopBar>
          <span className="round">
            <b>{template.title}</b>, a table for {snap.seats_wanted}
          </span>
          <ThemeToggle />
        </TopBar>
        <main className="wrap">
          <section className="hero">
            <WaitingRoom snap={snap} template={template} table={table} onChange={() => refresh()} />
          </section>
        </main>
      </>
    );
  }

  // The board stays up at the end; the composer's place takes the result card.
  const finished = ended && revealEnd && snap.status === "ended" ? ended : null;
  const parting = finished && endLine(finished, table.me);

  // A command the server refused comes back like a returned move, with its reason.
  function refused(e: unknown) {
    setReturned({ seat: table.me ?? "", outcome: "deterministic_invalid", reason_text: (e as Error).message, strikes: 0, nudge_text: null, state_version: snap!.state_version });
  }

  async function resign() {
    try {
      await api.resign(matchId, snap!.state_version);
    } catch (e) {
      refused(e);
    }
  }

  async function sendMove(moveText: string) {
    setPending(true);
    setReturned(null);
    try {
      await api.move(matchId, snap!.state_version, moveText, snap!.round_in_play);
    } catch (e) {
      setPending(false);
      refused(e);
      refresh();
    }
  }

  async function call() {
    if (!picked || calling) return;
    setCalling(true);
    try {
      await api.guess(matchId, snap!.state_version, picked, snap!.round_in_play);
    } catch (e) {
      setCalling(false);
      refused(e);
      refresh();
    }
  }

  if (snap.mode === "showcase") {
    return (
      <ShowcaseDuel
        snap={snap}
        template={template}
        table={table}
        spectator={spectator}
        text={text}
        setText={setText}
        pending={pending}
        returned={returned}
        ended={!!ended}
        send={() => sendMove(text)}
        resign={resign}
        formRef={formRef}
        picked={picked}
        setPicked={setPicked}
        calling={calling}
        call={call}
        finished={finished}
        parting={parting}
      />
    );
  }

  return (
    <EscalationDuel
      snap={snap}
      template={template}
      table={table}
      spectator={spectator}
      text={text}
      setText={setText}
      pending={pending}
      thinking={thinking}
      streaming={streaming}
      returned={returned}
      paused={paused}
      ended={ended}
      finished={finished}
      parting={parting}
      send={() => sendMove(fullMove(template.move_prefix, text))}
      resign={resign}
      formRef={formRef}
      transcriptRef={transcriptRef}
    />
  );
}

type EscalationProps = {
  snap: MatchSnapshot;
  template: TemplateView;
  table: Table;
  spectator: boolean;
  text: string;
  setText: (text: string) => void;
  pending: boolean;
  thinking: boolean;
  streaming: string;
  returned: TurnRejected | null;
  paused: JudgePaused | null;
  ended: MatchEnded | null;
  finished: MatchEnded | null;
  parting: { label: string | null; text: string } | null;
  send: () => Promise<void>;
  resign: () => Promise<void>;
  formRef: RefObject<HTMLFormElement | null>;
  transcriptRef: RefObject<HTMLOListElement | null>;
};

/** Turn by turn: the seats in the rail, the standing move in the middle, the move box under it. */
function EscalationDuel(props: EscalationProps) {
  const { snap, template, table, spectator, text, setText, pending, thinking, streaming, returned, paused, ended, finished, parting } = props;
  const { send, resign, formRef, transcriptRef } = props;
  const [showResign, setShowResign] = useState(false);
  const [showRubric, setShowRubric] = useState(false);
  const [voted, setVoted] = useState<Set<number>>(new Set());
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
  const standingText = streaming || standing?.move_text || snap.seed_token;
  const standingIsMine = !streaming && !!me && standing?.actor === me;
  const live = isLive(snap.status);
  const mover = table.seat(snap.to_move);
  const moverIsModel = mover?.kind === "model";
  const moverName = table.name(snap.to_move);
  const waiting = pending || thinking || !!streaming || (live && moverIsModel && !paused);
  const othersTurn = live && !moverIsModel && snap.to_move !== me && !waiting;
  const canPlay = snap.status === "active" && !!me && snap.to_move === me && !pending && !paused && !ended;
  const round = Math.min(snap.round_in_play, template.rounds_budget);
  const myIndex = snap.seats.findIndex((s) => s.seat === me);
  const after = snap.seats.filter((s, i) => i > myIndex && !s.eliminated).map((s) => table.name(s.seat));
  const lastRound = round === template.rounds_budget && snap.to_move === me && !!me;
  const totalAvailable = template.rubric.reduce((sum, r) => sum + r.max_points, 0);
  const longCard = isLongCard(snap.seed_token);
  const standingName = longCard && !standing && !streaming ? template.labels.opening.toLowerCase() : shortName(formName(standingText, prefix));
  const two = snap.seats.length === 2;
  const other = snap.seats.find((s) => s.seat !== me);

  function play(event: FormEvent) {
    event.preventDefault();
    if (!canPlay || !text.trim()) return;
    void send();
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    formRef.current?.requestSubmit();
  }

  const standingHead = fell
    ? `${table.name(fell.actor)} fell`
    : finished
    ? standing
      ? `${table.name(standing.actor)} had the last word`
      : template.labels.opening
    : streaming
    ? `${moverName} is writing`
    : standing
      ? standingIsMine
        ? "You wrote"
        : `From ${table.name(standing.actor)}`
      : template.labels.opening;
  const composerLabel = othersTurn
    ? `${moverName} is writing`
    : paused
    ? template.labels.compose_waiting
    : returned
      ? "Your move, still yours"
      : template.labels.compose.replace("{token}", standingName);
  const hostLine = finished
    ? parting!.text
    : fell
    ? "The match is over. One moment."
    : paused
    ? paused.host_text
    : returned?.nudge_text
      ? returned.nudge_text
      : waiting
        ? moverIsModel && !pending
          ? `${moverName} is thinking.`
          : "The judge is reading."
        : othersTurn
          ? `${capitalize(standingName)} stands. ${moverName} to move.`
          : `${capitalize(standingName)}. ${template.move_hint}`;

  return (
    <>
      <TopBar>
        <span className="round">
          {spectator && "Watching "}
          <b>{template.title}</b>, round {round} of {template.rounds_budget}
        </span>
        <ThemeToggle />
      </TopBar>

      <main className="stage">
        <section>
          <SeatList snap={snap} table={table} />
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
                    {turn.outcome === "forfeit" ? <em>Lost the turn</em> : turn.move_text}
                  </span>
                  {template.medallions && turn.outcome !== "forfeit" && (
                    <span className="medallion sm" role="img" aria-label={formName(turn.move_text, prefix)}>
                      {turn.host?.generated_emoji ?? "?"}
                    </span>
                  )}
                </div>
                {turn.outcome !== "forfeit" && (
                  <p className="ruling">
                    <span className="word">{rulingWord(turn)}</span>
                    <em>{turn.host?.headline}</em>
                  </p>
                )}
                {turn.played_by && <p className="ruling"><em>{turn.played_by} played this move for the House</em></p>}
              </li>
            ))}
            {paused && (
              <li>
                <div className="move">
                  <span>
                    <span className="who you">
                      <span>You</span>
                    </span>
                    {fullMove(prefix, text)}
                  </span>
                  {template.medallions && <span className="medallion sm empty">?</span>}
                </div>
                <p className="ruling">
                  <em>Waiting for the judge</em>
                </p>
              </li>
            )}
          </ol>
          {!spectator && !finished && me && (
            <p className="resign-row">
              <button className="quiet-button" type="button" onClick={() => setShowResign(true)} disabled={!!ended}>
                <Icon name="flag" />
                Resign the match
              </button>
            </p>
          )}
        </section>

        <section>
          <SeatStrip snap={snap} table={table} />
          {showYourSlip && !spectator && (
            <p className="your-slip">
              {template.medallions && (
                <span className="medallion" aria-hidden="true">
                  {yourLast.host?.generated_emoji}
                </span>
              )}
              <span>
                <b>
                  {rulingWord(yourLast)} for {formName(yourLast.move_text, prefix)}.
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
          {(!longCard || shown || streaming || paused) && (
            <>
              <p className="small-caps last-move-head">{standingHead}</p>
              <div className={`torn standing${waiting ? " reading" : ""}${finished ? " final" : ""}`}>
                {paused && <span className="tag">Awaiting ruling</span>}
                {shown && !streaming && !paused && (
                  <span className={`stamp corner${shown.outcome === "accept" ? "" : " ink"}`}>
                    {fell
                      ? stampText("Fell", fell.move_text, prefix)
                      : shown.outcome === "semantic_uncertain"
                        ? "Close call"
                        : stampText(`Point +${shown.points ?? 0}`, shown.move_text, prefix)}
                  </span>
                )}
                <p className="last-move">{paused ? fullMove(prefix, text) : fell ? fell.move_text : standingText}</p>
                {shown?.host && !streaming && !paused && (
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
          {paused && <p className="waiting">The match is paused until the judge rules.</p>}

          {finished && <ResultCard snap={snap} ended={finished} table={table} />}
          {!spectator && !finished && me && (
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
              {lastRound && (
                <p className="last-round">
                  {after.length > 0
                    ? `Last round: ${after.join(", ")} ${after.length === 1 ? "answers" : "answer"} once more, then it’s over.`
                    : "Last round: your move is the last of the match."}
                </p>
              )}
              <div className={`compose-box${waiting && !paused ? " scanning" : ""}`}>
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
                  disabled={pending || !!ended || (snap.to_move !== me && !paused)}
                  placeholder={paused ? "thinking ahead. It sends when play resumes." : template.move_example.slice(prefix.length)}
                />
              </div>
              {returned && (
                <p className="why">
                  <b>Not a move yet.</b> {returned.reason_text}
                </p>
              )}
              <div className="composer-foot">
                <span className="counter" aria-live="polite">
                  {canPlay && left !== null && <b className="clock">{clockText(left)} left · </b>}
                  {fullMove(prefix, text).length}/{template.max_chars}
                </span>
                <button className={`ticket${canPlay ? "" : " quiet"}`} type="submit" disabled={!canPlay}>
                  {pending ? "Sent" : "Play it"}
                </button>
              </div>
            </form>
          )}
          {(spectator || !me) && !waiting && !paused && live && <p className="waiting">Waiting for {moverName} to move.</p>}

          {waiting && !paused && (
            <div className="judge-reading" role="status">
              <Host state="thinking" />
              <span>
                {streaming ? `${moverName} is answering` : "The judge is reading"}
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
                  <span>{latest.actor === me ? "Your move" : `${table.name(latest.actor)}'s move`}</span>
                  <span>The judge was {latest.scoring.confidence}</span>
                </p>
                <dl className="points">
                  {template.rubric.map((entry) => {
                    const earned = (latest.scoring?.scores[entry.name] ?? 0) * (entry.max_points / template.score_max);
                    const decided = entry.name === latest.host?.because_clause.criterion;
                    return (
                      <div key={entry.name} className={decided ? "decided" : undefined}>
                        <dt>
                          {criterionLabel(entry.name, template)}
                          {decided && <span className="decided-tag">decided it</span>}
                        </dt>
                        <dd>
                          <b>{earned}</b> of {entry.max_points}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
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
                      {criterionLabel(entry.name, template)} <small>up to {entry.max_points}</small>
                    </summary>
                    <p>{entry.description}</p>
                  </details>
                ))}
              </div>
            </>
          )}
          <div className="host" style={{ marginTop: "var(--space-3)" }}>
            <Host state={finished ? "tipping" : paused || waiting ? "thinking" : "idle"} />
            <p className={`host-line${parting?.label ? " coaching" : ""}`}>
              {parting?.label && <span>{parting.label}</span>}
              {hostLine}
            </p>
          </div>
        </section>
      </main>

      {showResign && (
        <ResignSheet
          text={
            two && other
              ? `${table.name(other.seat)} takes the win at ${(me && table.seat(me)?.points) ?? 0} : ${other.points}.`
              : "You leave the table and the others play on."
          }
          onResign={() => {
            setShowResign(false);
            void resign();
          }}
          onKeep={() => setShowResign(false)}
        />
      )}
    </>
  );
}

function ResignSheet({ text, onResign, onKeep }: { text: string; onResign: () => void; onKeep: () => void }) {
  return (
    <div className="scrim">
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="give-up-title" style={{ maxWidth: "26rem" }}>
        <h2 id="give-up-title" style={{ margin: 0, fontSize: "1.8rem" }}>
          Resign this match?
        </h2>
        <p style={{ margin: "var(--space-2) 0 0", color: "var(--ink-soft)" }}>{text} The replay is saved either way.</p>
        <div className="sheet-actions">
          <button className="quiet-button" type="button" onClick={onResign}>
            <Icon name="flag" />
            Resign
          </button>
          <button className="ticket" type="button" onClick={onKeep} autoFocus>
            Keep playing
          </button>
        </div>
      </div>
    </div>
  );
}

function rulingWord(turn: TurnView): string {
  if (turn.outcome === "forfeit") return "Lost the turn";
  if (turn.outcome === "fail") return "Fell";
  if (turn.outcome === "semantic_uncertain") return `Close call, ${turn.points ?? 0}`;
  return `Point, ${turn.points ?? 0}`;
}

type ShowcaseProps = {
  snap: MatchSnapshot;
  template: TemplateView;
  table: Table;
  spectator: boolean;
  text: string;
  setText: (text: string) => void;
  pending: boolean;
  returned: TurnRejected | null;
  ended: boolean;
  send: () => Promise<void>;
  resign: () => Promise<void>;
  formRef: RefObject<HTMLFormElement | null>;
  picked: string | null;
  setPicked: (key: string) => void;
  calling: boolean;
  call: () => Promise<void>;
  finished: MatchEnded | null;
  parting: { label: string | null; text: string } | null;
};

/** Everyone at once: one card per round, every seat writes, the call once all are judged, all
 *  answers shown together once the truth is out. */
function ShowcaseDuel(props: ShowcaseProps) {
  const { snap, template, table, spectator, text, setText, pending, returned, ended, formRef, picked, setPicked, calling, call, finished, parting } = props;
  const { send, resign } = props;
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
  const hostLine = finished
    ? parting!.text
    : ended
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

  function play(event: FormEvent) {
    event.preventDefault();
    if (!canWrite || !text.trim()) return;
    void send();
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    formRef.current?.requestSubmit();
  }

  return (
    <>
      <TopBar>
        <span className="round">
          {spectator && "Watching "}
          <b>{template.title}</b>, round {roundN} of {template.rounds_budget}
        </span>
        <ThemeToggle />
      </TopBar>

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
              onPick={setPicked}
              onCall={call}
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
            <form ref={formRef} className={`composer${returned ? " returned" : ""}`} style={{ marginTop: "var(--space-3)" }} onSubmit={play}>
              {returned && (
                <span className="slip returned-slip" aria-hidden="true">
                  Returned, try again
                </span>
              )}
              <label className="small-caps" htmlFor="move">
                {template.labels.compose.replace("{token}", shortName(current.round.token))}
              </label>
              <div className="compose-box bare">
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
                  disabled={pending || ended}
                  placeholder={template.move_example}
                />
              </div>
              {returned && (
                <p className="why">
                  <b>Not a definition yet.</b> {returned.reason_text}
                </p>
              )}
              <div className="composer-foot">
                <span className="counter" aria-live="polite">
                  {clock && <b className="clock">{clock} left · </b>}
                  {text.length}/{template.max_chars}
                </span>
                <button className={`ticket${canWrite ? "" : " quiet"}`} type="submit" disabled={!canWrite}>
                  {pending ? "Sent" : "Play it"}
                </button>
              </div>
            </form>
          )}
          {out && !finished && <p className="waiting">You are out of this match. The others play on.</p>}

          {!spectator && !finished && me && !out && (
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
              <details key={entry.name} open={revealed.length === 0}>
                <summary>
                  {criterionLabel(entry.name, template)} <small>up to {entry.max_points}</small>
                </summary>
                <p>{entry.description}</p>
              </details>
            ))}
          </div>
          <ul className="rules-text">
            {template.rules.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
          <div className="host" style={{ marginTop: "var(--space-3)" }}>
            <Host state={finished ? "tipping" : judging ? "thinking" : "idle"} />
            <p className={`host-line${parting?.label ? " coaching" : ""}`}>
              {parting?.label && <span>{parting.label}</span>}
              {hostLine}
            </p>
          </div>
        </section>
      </main>

      {showResign && (
        <ResignSheet
          text={snap.seats.length === 2 ? "The other seat takes the win." : "You leave the table and the others play on."}
          onResign={() => {
            setShowResign(false);
            void resign();
          }}
          onKeep={() => setShowResign(false)}
        />
      )}
    </>
  );
}
