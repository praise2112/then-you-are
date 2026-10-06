import { useEffect } from "react";

import type { components } from "./generated/openapi";

type S = components["schemas"];
export type TemplateView = S["TemplateView"];
export type MatchSnapshot = S["MatchSnapshot"];
export type Replay = S["Replay"];
export type StageView = S["StageView"];
export type SessionView = S["SessionView"];
export type AccountView = S["AccountView"];
export type BoardView = S["BoardView"];
export type BoardSummary = S["BoardSummary"];
export type ProfileView = S["ProfileView"];
export type DuelRow = S["DuelRow"];
export type OpenDuel = S["OpenDuel"];
export type ReplaySort = "curated" | "newest" | "longest";
export type TurnView = S["TurnView"];
export type DemoPoints = S["DemoPoints"];
export type RoundView = S["RoundView"];
export type RoundRevealed = S["RoundRevealed"];
export type GuessOpened = S["GuessOpened"];
export type GuessOption = S["GuessOption"];
export type GuessView = S["GuessView"];
export type Ruling = S["Ruling"];
export type TurnRejected = S["TurnRejected"];
export type MatchEnded = S["MatchEnded"];
export type MoveToken = S["MoveToken"];
export type JudgePaused = S["JudgePaused"];
export type ScoringPayload = S["ScoringPayload"];
export type HostPayload = S["HostPayload"];
export type SeatView = S["SeatView"];
export type TableView = S["TableView"];
export type TurnChanged = S["TurnChanged"];
export type SocketMessage = S["Online"] | S["Lobby"] | S["TurnNudge"];

export type MatchEvent =
  | { name: "turn_rejected"; data: TurnRejected }
  | { name: "judge_started"; data: S["JudgeStarted"] }
  | { name: "ruling"; data: Ruling }
  | { name: "move_token"; data: MoveToken }
  | { name: "judge_paused"; data: JudgePaused }
  | { name: "judge_resumed"; data: S["JudgeResumed"] }
  | { name: "match_ended"; data: MatchEnded }
  | { name: "round_revealed"; data: RoundRevealed }
  | { name: "guess_opened"; data: GuessOpened }
  | { name: "seat_joined"; data: S["SeatJoined"] }
  | { name: "match_started"; data: S["MatchStarted"] }
  | { name: "seat_submitted"; data: S["SeatSubmitted"] }
  | { name: "turn_changed"; data: TurnChanged };

const EVENT_NAMES: MatchEvent["name"][] = [
  "turn_rejected",
  "judge_started",
  "ruling",
  "move_token",
  "judge_paused",
  "judge_resumed",
  "match_ended",
  "round_revealed",
  "guess_opened",
  "seat_joined",
  "match_started",
  "seat_submitted",
  "turn_changed",
];

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  templates: () => request<TemplateView[]>("/templates"),
  template: (slug: string) => request<TemplateView>(`/templates/${slug}`),
  createMatch: (
    templateId: string,
    opts: { stageName?: string; seedToken?: string; firstMove?: string; friends?: boolean; seats?: number } = {},
  ) =>
    request<MatchSnapshot>("/matches", {
      method: "POST",
      body: JSON.stringify({
        template_id: templateId,
        stage_name: opts.stageName || null,
        seed_token: opts.seedToken ?? null,
        first_move: opts.firstMove ?? null,
        kind: opts.friends ? "friends" : "house",
        seats: opts.seats ?? 2,
      }),
    }),
  joinTable: (inviteCode: string, stageName?: string) =>
    request<{ match_id: string }>("/tables/join", {
      method: "POST",
      body: JSON.stringify({ invite_code: inviteCode, stage_name: stageName || null }),
    }),
  quickMatch: (templateId: string, seats: number, stageName?: string) =>
    request<{ match_id: string }>("/tables/quick", {
      method: "POST",
      body: JSON.stringify({ template_id: templateId, seats, stage_name: stageName || null }),
    }),
  addHouse: (id: string) => request<void>(`/matches/${id}/seats/house`, { method: "POST" }),
  lobby: () => request<TableView[]>("/tables"),
  match: (id: string) => request<MatchSnapshot>(`/matches/${id}`),
  move: (id: string, expectedVersion: number, moveText: string, roundN: number) =>
    request<void>(`/matches/${id}/moves`, {
      method: "POST",
      body: JSON.stringify({
        action_id: crypto.randomUUID(),
        expected_version: expectedVersion,
        move_text: moveText,
        round_n: roundN,
      }),
    }),
  guess: (id: string, expectedVersion: number, key: string, roundN: number) =>
    request<void>(`/matches/${id}/guesses`, {
      method: "POST",
      body: JSON.stringify({ action_id: crypto.randomUUID(), expected_version: expectedVersion, key, round_n: roundN }),
    }),
  resign: (id: string, expectedVersion: number) =>
    request<void>(`/matches/${id}/resign`, {
      method: "POST",
      body: JSON.stringify({ action_id: crypto.randomUUID(), expected_version: expectedVersion }),
    }),
  disagree: (id: string, seq: number) =>
    request<void>(`/matches/${id}/turns/${seq}/disagree`, { method: "POST" }),
  session: () => request<SessionView>("/sessions/me"),
  updateSession: (body: { stage_name?: string; list_duels?: boolean }) =>
    request<SessionView>("/sessions/me", { method: "PUT", body: JSON.stringify(body) }),
  setVisibility: (id: string, isPublic: boolean) =>
    request<void>(`/matches/${id}/visibility`, { method: "POST", body: JSON.stringify({ public: isPublic }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  deleteAccount: () => request<void>("/sessions/me/account", { method: "DELETE" }),
  boards: () => request<BoardSummary[]>("/leaderboard"),
  board: (slug: string) => request<BoardView>(`/leaderboard/${slug}`),
  rankedPlayers: () => request<number>("/leaderboard/players"),
  profile: (id: string) => request<ProfileView>(`/profiles/${id}`),
  stage: () => request<StageView>("/on-stage"),
  replay: (id: string) => request<Replay>(`/replays/${id}`),
  replays: (sort: ReplaySort) => request<Replay[]>(`/replays?sort=${sort}`),
  curate: (id: string, curated: boolean, token: string) =>
    request<void>(`/replays/${id}/curate`, {
      method: "POST",
      headers: { "x-curator-token": token },
      body: JSON.stringify({ curated }),
    }),
};

export function useMatchEvents(matchId: string | null, onEvent: (event: MatchEvent) => void) {
  useEffect(() => {
    if (!matchId) return;
    const source = new EventSource(`/matches/${matchId}/events`);
    const handlers = EVENT_NAMES.map((name) => {
      const handler = (raw: MessageEvent) =>
        onEvent({ name, data: JSON.parse(raw.data) } as MatchEvent);
      source.addEventListener(name, handler);
      return [name, handler] as const;
    });
    return () => {
      handlers.forEach(([name, handler]) => source.removeEventListener(name, handler));
      source.close();
    };
  }, [matchId, onEvent]);
}

/** The site-wide socket: who is online, the open tables, and your turn elsewhere. Reconnects
 *  after a drop; a page without a session gets nothing. */
export function usePresence(onMessage: (message: SocketMessage) => void) {
  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    const open = () => {
      socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
      socket.onmessage = (raw) => onMessage(JSON.parse(raw.data) as SocketMessage);
      socket.onclose = (event) => {
        if (!closed && event.code !== 4401) retry = setTimeout(open, 3000);
      };
    };
    open();
    const beat = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send("heartbeat"), 20000);
    return () => {
      closed = true;
      clearTimeout(retry);
      clearInterval(beat);
      socket?.close();
    };
  }, [onMessage]);
}
