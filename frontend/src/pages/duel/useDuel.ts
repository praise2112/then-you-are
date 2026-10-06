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
  type Ruling,
  type TemplateView,
  type TurnRejected,
  type TurnView,
} from "../../api.ts";
import { store } from "../../store.ts";
import { fullMove, isLive, withoutPrefix } from "../format.ts";

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

function withTotals(s: MatchSnapshot, totals: Record<string, number>): MatchSnapshot["seats"] {
  return s.seats.map((seat) => ({ ...seat, points: totals[seat.seat] ?? seat.points }));
}

function lastSeq(s: MatchSnapshot | null): number {
  return s?.transcript[s.transcript.length - 1]?.seq ?? 0;
}

function withRuling(s: MatchSnapshot, r: Ruling): MatchSnapshot {
  const fresh = !s.transcript.some((t) => t.seq === r.seq);
  return {
    ...s,
    status: "active",
    state_version: r.state_version,
    to_move: r.to_move,
    round_in_play: r.round_in_play,
    seats: withTotals(s, r.totals),
    judged_moves: s.judged_moves + (fresh ? 1 : 0),
    transcript: fresh ? [...s.transcript, turnOf(r)] : s.transcript,
  };
}

/** A match as one seat, or a spectator, sees it: the snapshot and template kept current from the
 *  event stream, what the seat is writing, and the move, call and resign commands. */
export function useDuel(matchId: string, spectator: boolean) {
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
  // What the event handler reads, so it stays the same function across renders.
  const snapRef = useRef(snap);
  const templateRef = useRef(template);
  const pendingRef = useRef(pending);
  // The newest state version the client holds or has a fetch under way for.
  const versionRef = useRef(-1);
  const fetchingRef = useRef(false);
  const behindRef = useRef(false);
  // How many snapshot fetches have started; a fetch numbered above `since` was read after it.
  const fetchesRef = useRef(0);
  // The move the judge or a House writer is working on, while the board shows it.
  const busyRef = useRef<{ seq: number; since: number } | null>(null);
  // The seat's own move with the judge, sent after turn afterSeq.
  const sentRef = useRef<{ afterSeq: number; since: number } | null>(null);
  // The state version the seat's latest move was sent at; a refusal at or below it is for an earlier one.
  const sendVersionRef = useRef(-1);
  // The showcase round that what the seat wrote, sent or picked belongs to.
  const roundRef = useRef(0);

  // The move numbered seq, or one after it, is resolved: the board stops showing it as under way.
  const endBusy = useCallback((seq: number) => {
    if (!busyRef.current || busyRef.current.seq > seq) return;
    busyRef.current = null;
    setThinking(false);
    setStreaming("");
    setPaused(null);
  }, []);

  // The seat's own move is resolved; a ruled move leaves the composer for the next one.
  const endSent = useCallback(
    (ruled: boolean) => {
      const sent = sentRef.current;
      if (!sent) return;
      sentRef.current = null;
      setPending(false);
      endBusy(sent.afterSeq + 1);
      if (!ruled) return;
      setReturned(null);
      setText("");
    },
    [endBusy],
  );

  // A showcase round closed: what the seat wrote, sent or picked for it is done with.
  const startRound = useCallback((round: number) => {
    if (round <= roundRef.current) return;
    roundRef.current = round;
    setCalling(false);
    setPicked(null);
    setReturned(null);
    setPending(false);
    setText("");
  }, []);

  const refresh = useCallback(() => {
    if (fetchingRef.current) {
      behindRef.current = true;
      return;
    }
    fetchingRef.current = true;
    const fetchNo = ++fetchesRef.current;
    api
      .match(matchId)
      .then((s) => {
        if (s.state_version < versionRef.current) return;
        versionRef.current = s.state_version;
        setSnap(s);
        if (s.mode === "showcase") startRound(s.round_in_play);
        // A human to move with nothing before the judge means the move under way came back.
        const humanFree = s.status === "active" && s.seats.find((x) => x.seat === s.to_move)?.kind === "human";
        endBusy(humanFree && fetchNo > (busyRef.current?.since ?? Infinity) ? Infinity : lastSeq(s));
        const me = spectator ? null : s.your_seat;
        const withJudge = !!me && s.to_move === me && (s.status === "awaiting_judgment" || s.status === "paused");
        const sent = sentRef.current;
        if (withJudge && !sent) sentRef.current = { afterSeq: lastSeq(s), since: 0 };
        if (!withJudge && sent && fetchNo > sent.since) {
          endSent(s.transcript.some((t) => t.actor === me && t.seq > sent.afterSeq));
        }
        if (s.status === "open" || isLive(s.status)) setAfter((a) => a ?? s.event_id);
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
  }, [matchId, spectator, endBusy, endSent, startRound]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const templateId = snap?.template_id;
  useEffect(() => {
    if (!templateId) return;
    api.template(templateId).then(setTemplate, (e) => setError(e.message));
  }, [templateId]);

  useEffect(() => {
    snapRef.current = snap;
    templateRef.current = template;
    pendingRef.current = pending;
  }, [snap, template, pending]);

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
      const showcase = snapRef.current?.mode === "showcase";
      const mySeat = spectator ? null : (snapRef.current?.your_seat ?? null);
      // A replayed event about a move the snapshot already holds.
      const resolved = (seq: number) => seq <= lastSeq(snapRef.current);
      const markBusy = (seq: number) => {
        if (busyRef.current?.seq !== seq) busyRef.current = { seq, since: fetchesRef.current };
      };
      switch (event.name) {
        case "judge_started":
          if (resolved(event.data.seq)) return;
          markBusy(event.data.seq);
          setThinking(true);
          return;
        case "turn_rejected": {
          const r = event.data;
          const mine = r.seat === mySeat && r.state_version > sendVersionRef.current;
          if (mine) {
            sentRef.current = null;
            setThinking(false);
            setPending(false);
          }
          // A showcase refusal says why only in the refused seat's own snapshot.
          if (showcase) {
            if (mine) catchUp(r.state_version);
            return;
          }
          if (mine) setReturned(r);
          advance(r.state_version, (s) => ({ ...s, status: "active", state_version: r.state_version }));
          return;
        }
        case "move_token":
          if (resolved(event.data.seq)) return;
          markBusy(event.data.seq);
          setStreaming((s) => s + event.data.text);
          return;
        case "judge_paused": {
          const p = event.data;
          if (resolved(p.seq)) return;
          markBusy(p.seq);
          setPaused(p);
          if (p.seq === (sentRef.current?.afterSeq ?? -1) + 1) {
            setText((t) => t || withoutPrefix(p.move_text, templateRef.current?.move_prefix ?? ""));
          }
          return;
        }
        case "judge_resumed":
          setPaused(null);
          return;
        case "ruling": {
          // A showcase round's rulings arrive together with its reveal, read from the snapshot.
          if (showcase) return;
          const r = event.data;
          endBusy(r.seq);
          if (r.actor === mySeat && r.seq > (sentRef.current?.afterSeq ?? Infinity)) endSent(true);
          advance(r.state_version, (s) => withRuling(s, r));
          return;
        }
        case "round_revealed":
          startRound(event.data.round_n + 1);
          catchUp(event.data.state_version);
          return;
        case "guess_opened":
          if (event.data.round_n >= roundRef.current) {
            setPending(false);
            setPicked(null);
            setCalling(false);
          }
          catchUp(event.data.state_version);
          return;
        case "seat_submitted": {
          const { seat, state_version } = event.data;
          if (state_version > versionRef.current) return catchUp(state_version);
          if (state_version < versionRef.current) return;
          // A seat's last answer or call is announced after the change it made, which may have
          // closed the round; only the seat's own move still pending is sure to be for this round.
          if (seat !== mySeat || !pendingRef.current) return refresh();
          patch((s) => ({ ...s, seats: s.seats.map((x) => (x.seat === seat ? { ...x, answered: true } : x)) }));
          return;
        }
        case "turn_changed": {
          const t = event.data;
          if (t.state_version > versionRef.current) return catchUp(t.state_version);
          if (t.state_version < versionRef.current) return;
          // Sending a move leaves the version as it was, so while a move is under way only a fresh
          // snapshot tells a hand-back from a turn change sent before the move.
          const status = snapRef.current?.status;
          const underWay = sentRef.current || busyRef.current || status === "awaiting_judgment" || status === "paused";
          if (underWay) return refresh();
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
          // A snapshot read before the end must not bring the match back.
          if (showcase) catchUp(e.state_version);
          else versionRef.current = Math.max(versionRef.current, e.state_version);
          setThinking(false);
          setStreaming("");
          setSnap((s) => s && { ...s, status: "ended", winner: e.winner, end_reason: e.end_reason, seats: withTotals(s, e.totals) });
          setEnded(e);
          setTimeout(() => (spectator ? navigate(`/r/${matchId}`) : setRevealEnd(true)), e.end_reason === "resign" ? 0 : 3200);
          return;
        }
      }
    },
    [refresh, endBusy, endSent, startRound, spectator, matchId],
  );
  useMatchEvents(matchId, after, onEvent, refresh);

  // The board stays up at the end; the composer's place takes the result card.
  const finished = ended && revealEnd && snap?.status === "ended" ? ended : null;

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
    const showcase = snap!.mode === "showcase";
    const moveText = showcase ? text : fullMove(template!.move_prefix, text);
    const sent = showcase ? null : { afterSeq: lastSeq(snap), since: Infinity };
    sentRef.current = sent;
    sendVersionRef.current = snap!.state_version;
    const fetchesBefore = fetchesRef.current;
    try {
      await api.move(matchId, snap!.state_version, moveText, snap!.round_in_play);
      if (!sent) return;
      sent.since = fetchesRef.current;
      // A fetch that started while the move was on its way may predate it.
      if (fetchesRef.current > fetchesBefore && sentRef.current === sent) refresh();
    } catch (e) {
      if (sentRef.current === sent) sentRef.current = null;
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
    thinking,
    streaming,
    returned,
    paused,
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
