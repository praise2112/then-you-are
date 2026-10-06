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
    api.template(templateId).then(setTemplate, (e) => setError(e.message));
  }, [templateId]);

  useEffect(() => {
    snapRef.current = snap;
    templateRef.current = template;
  }, [snap, template]);

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
      switch (event.name) {
        case "judge_started":
          setThinking(true);
          return;
        case "turn_rejected": {
          const r = event.data;
          const mine = r.seat === mySeat;
          if (mine) {
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
          setStreaming((s) => s + event.data.text);
          return;
        case "judge_paused":
          setPaused(event.data);
          setText((t) => t || withoutPrefix(event.data.move_text, templateRef.current?.move_prefix ?? ""));
          return;
        case "judge_resumed":
          setPaused(null);
          return;
        case "ruling": {
          // A showcase round's rulings arrive together with its reveal, read from the snapshot.
          if (showcase) return;
          const r = event.data;
          if (r.state_version <= versionRef.current) return;
          setThinking(false);
          setStreaming("");
          if (r.actor === mySeat) {
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
          if (showcase) catchUp(e.state_version);
          return;
        }
      }
    },
    [refresh, spectator, matchId],
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
    const moveText = snap!.mode === "showcase" ? text : fullMove(template!.move_prefix, text);
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
