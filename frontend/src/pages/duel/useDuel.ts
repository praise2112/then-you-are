import { useCallback, useEffect, useRef, useState } from "react";

import { navigate } from "../../App.tsx";
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
} from "../../api.ts";
import { store } from "../../store.ts";
import { fullMove, isLive } from "../format.ts";

function endedFromReplay(r: Replay): MatchEnded {
  // Walking back into a finished match: the coaching line is on the move that fell.
  const last = r.transcript[r.transcript.length - 1];
  return {
    end_reason: r.end_reason!,
    result_kind: r.result_kind,
    winner: r.winner,
    totals: Object.fromEntries(r.seats.map((s) => [s.seat, s.points])),
    highlight_seq: r.highlight_seq,
    coaching_line: last?.outcome === "fail" ? (last.host?.coaching_line ?? null) : null,
    share_text: r.share_text,
    replay_id: r.id,
    state_version: r.state_version,
  };
}

function lastSeq(s: MatchSnapshot | null): number {
  return s?.transcript[s.transcript.length - 1]?.seq ?? 0;
}

// The judge or a House seat is still working on move seq, as the snapshot shows it.
function isUnderWay(s: MatchSnapshot, seq: number): boolean {
  if (seq <= lastSeq(s)) return false;
  const houseToMove = s.seats.find((x) => x.seat === s.to_move)?.kind === "model";
  return s.status === "awaiting_judgment" || s.status === "paused" || (s.status === "active" && houseToMove);
}

// The seat's own escalation move is with the judge.
function isWithJudge(s: MatchSnapshot, seat: string | null): boolean {
  return s.to_move === seat && (s.status === "awaiting_judgment" || s.status === "paused");
}

// Escalation holds the falling move on the board for a beat before the result card.
function revealDelay(s: MatchSnapshot | null, reason: MatchEnded["end_reason"]): number {
  return s?.mode === "escalation" && reason !== "resign" ? 3200 : 0;
}

type Busy = { seq: number; thinking: boolean; streaming: string; paused: JudgePaused | null };
type Sent = { afterSeq: number; since: number };

/** A match as one seat, or a spectator, sees it: the snapshot, fetched again whenever the event
 *  stream says it changed, what the seat is writing, and the move, call and resign commands. */
export function useDuel(matchId: string, spectator: boolean) {
  const [snap, setSnap] = useState<MatchSnapshot | null>(null);
  const [template, setTemplate] = useState<TemplateView | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [returned, setReturned] = useState<TurnRejected | null>(null);
  // What the judge or a House writer is doing on the move under way, from the stream alone.
  const [busy, setBusy] = useState<Busy | null>(null);
  const [ended, setEnded] = useState<MatchEnded | null>(null);
  const [revealEnd, setRevealEnd] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [calling, setCalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [after, setAfter] = useState<string | null>(null);
  const snapRef = useRef(snap);
  // The newest state version the stream or a snapshot has shown; older snapshots are dropped.
  const knownRef = useRef(-1);
  const fetchingRef = useRef(false);
  const queuedRef = useRef(false);
  const fetchesRef = useRef(0);
  // The seat's move out with the judge: the turn it follows, and the fetch count when it was acknowledged.
  const sentRef = useRef<Sent | null>(null);
  const endedRef = useRef(false);

  const finish = useCallback(
    (e: MatchEnded, delay: number) => {
      endedRef.current = true;
      setEnded(e);
      setTimeout(() => (spectator ? navigate(`/r/${matchId}`) : setRevealEnd(true)), delay);
    },
    [spectator, matchId],
  );

  // Fetches the snapshot, one at a time; calls while one is waiting or under way share one more.
  const refresh = useCallback(() => {
    const run = () => {
      queuedRef.current = false;
      fetchingRef.current = true;
      const fetchNo = ++fetchesRef.current;
      api
        .match(matchId)
        .then((s) => {
          if (s.state_version < knownRef.current) return;
          knownRef.current = s.state_version;
          const prev = snapRef.current;
          snapRef.current = s;
          setSnap(s);
          setBusy((b) => (b && isUnderWay(s, b.seq) ? b : null));
          const me = spectator ? null : s.your_seat;
          const sent = sentRef.current;
          if (sent && fetchNo > sent.since && !isWithJudge(s, me)) {
            sentRef.current = null;
            setPending(false);
            if (s.transcript.some((t) => t.actor === me && t.seq > sent.afterSeq)) {
              setText("");
              setReturned(null);
            }
          }
          if (s.mode === "showcase" && prev && s.round_in_play !== prev.round_in_play) {
            setText("");
            setReturned(null);
            setPending(false);
          }
          if (s.mode === "showcase" && prev && (s.round_in_play !== prev.round_in_play || s.phase !== prev.phase)) {
            setPicked(null);
            setCalling(false);
          }
          // A showcase refusal says why only in the refused seat's own snapshot.
          if (s.mode === "showcase" && s.returned) setReturned(s.returned);
          if (s.status === "open" || isLive(s.status)) setAfter((a) => a ?? s.event_id);
          const opening = store.takeOpeningMove(matchId);
          if (opening) {
            setText(opening);
            if (s.status === "awaiting_judgment") {
              setPending(true);
              sentRef.current = { afterSeq: lastSeq(s), since: fetchNo };
            }
          }
          if (s.status === "ended" && s.end_reason && !endedRef.current) {
            endedRef.current = true;
            const delay = revealDelay(prev, s.end_reason);
            return api.replay(matchId).then((r) => finish(endedFromReplay(r), delay));
          }
        })
        .catch((e) => setError(e.message))
        .finally(() => {
          fetchingRef.current = false;
          if (queuedRef.current) run();
        });
    };
    if (queuedRef.current) return;
    queuedRef.current = true;
    // Events that arrive together share the fetch.
    if (!fetchingRef.current) setTimeout(run);
  }, [matchId, spectator, finish]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const templateId = snap?.template_id;
  useEffect(() => {
    if (!templateId) return;
    api.template(templateId).then(setTemplate, (e) => setError(e.message));
  }, [templateId]);

  const onEvent = useCallback(
    (event: MatchEvent) => {
      // Shows what the judge or a House writer does on move seq, unless the snapshot holds it.
      const onMove = (seq: number, update: (b: Busy) => Busy) => {
        if (seq <= lastSeq(snapRef.current)) return;
        setBusy((b) => update(b?.seq === seq ? b : { seq, thinking: false, streaming: "", paused: null }));
      };
      switch (event.name) {
        case "judge_started":
          return onMove(event.data.seq, (b) => ({ ...b, thinking: true }));
        case "move_token":
          return onMove(event.data.seq, (b) => ({ ...b, streaming: b.streaming + event.data.text }));
        case "judge_paused":
          return onMove(event.data.seq, (b) => ({ ...b, paused: event.data }));
        case "judge_resumed":
          return onMove(event.data.seq, (b) => ({ ...b, paused: null }));
      }
      const version = event.data.state_version;
      // Filling a table leaves the version at 0.
      if (version > 0 && version < knownRef.current) return;
      const s = snapRef.current;
      if (event.name === "turn_rejected" && s?.mode === "escalation" && version > knownRef.current) {
        // An escalation refusal says why only on the stream.
        if (!spectator && event.data.seat === s.your_seat) setReturned(event.data);
      }
      if (event.name === "match_ended" && event.data.end_reason !== "unfilled" && !endedRef.current) {
        finish(event.data, revealDelay(s, event.data.end_reason));
      }
      knownRef.current = Math.max(knownRef.current, version);
      refresh();
    },
    [refresh, finish, spectator],
  );
  useMatchEvents(matchId, after, onEvent, refresh);

  // The board stays up at the end; the composer's place takes the result card.
  const finished = ended && revealEnd ? ended : null;

  // A command the server refused comes back like a returned move, with its reason.
  function refused(e: unknown) {
    const seat = spectator ? "" : (snap!.your_seat ?? "");
    setReturned({ seat, outcome: "deterministic_invalid", reason_text: (e as Error).message, strikes: 0, nudge_text: null, state_version: snap!.state_version });
  }

  async function resign() {
    try {
      await api.resign(matchId, snap!.state_version);
    } catch (e) {
      refused(e);
    }
  }

  /** Sends what the seat wrote; an escalation move gets the template's prefix. */
  async function move() {
    setPending(true);
    setReturned(null);
    const moveText = snap!.mode === "showcase" ? text : fullMove(template!.move_prefix, text);
    const sent = { afterSeq: lastSeq(snap), since: Infinity };
    sentRef.current = sent;
    const fetchesBefore = fetchesRef.current;
    try {
      await api.move(matchId, snap!.state_version, moveText, snap!.round_in_play);
      sent.since = fetchesRef.current;
      // A fetch that started while the move was on its way may predate it.
      if (fetchesRef.current > fetchesBefore) refresh();
    } catch (e) {
      sentRef.current = null;
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

  return {
    snap,
    template,
    error,
    spectator,
    text,
    setText,
    pending,
    thinking: !!busy?.thinking,
    streaming: busy?.streaming ?? "",
    returned,
    paused: busy?.paused ?? null,
    ended,
    finished,
    picked,
    setPicked,
    calling,
    refresh,
    move,
    call,
    resign,
  };
}

export type DuelState = ReturnType<typeof useDuel>;
