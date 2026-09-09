import { useEffect } from "react";

import type { components } from "./generated/openapi";

type S = components["schemas"];
export type TemplateView = S["TemplateView"];
export type MatchSnapshot = S["MatchSnapshot"];
export type Replay = S["Replay"];
export type StageView = S["StageView"];
export type ReplaySort = "curated" | "newest" | "longest";
export type TurnView = S["TurnView"];
export type Ruling = S["Ruling"];
export type TurnRejected = S["TurnRejected"];
export type MatchEnded = S["MatchEnded"];
export type MoveToken = S["MoveToken"];
export type JudgePaused = S["JudgePaused"];
export type ScoringPayload = S["ScoringPayload"];
export type HostPayload = S["HostPayload"];

export type MatchEvent =
  | { name: "turn_rejected"; data: TurnRejected }
  | { name: "judge_started"; data: S["JudgeStarted"] }
  | { name: "ruling"; data: Ruling }
  | { name: "move_token"; data: MoveToken }
  | { name: "judge_paused"; data: JudgePaused }
  | { name: "judge_resumed"; data: S["JudgeResumed"] }
  | { name: "match_ended"; data: MatchEnded }
  | { name: "state_resync"; data: S["StateResync"] };

const EVENT_NAMES: MatchEvent["name"][] = [
  "turn_rejected",
  "judge_started",
  "ruling",
  "move_token",
  "judge_paused",
  "judge_resumed",
  "match_ended",
  "state_resync",
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
  template: () => request<TemplateView>("/templates/then-i-am"),
  createMatch: (opts: { stageName?: string; seedToken?: string; firstMove?: string } = {}) =>
    request<MatchSnapshot>("/matches", {
      method: "POST",
      body: JSON.stringify({
        template_id: "then-i-am",
        stage_name: opts.stageName || null,
        seed_token: opts.seedToken ?? null,
        first_move: opts.firstMove ?? null,
      }),
    }),
  match: (id: string) => request<MatchSnapshot>(`/matches/${id}`),
  move: (id: string, expectedVersion: number, moveText: string) =>
    request<void>(`/matches/${id}/moves`, {
      method: "POST",
      body: JSON.stringify({
        action_id: crypto.randomUUID(),
        expected_version: expectedVersion,
        move_text: moveText,
      }),
    }),
  resign: (id: string, expectedVersion: number) =>
    request<void>(`/matches/${id}/resign`, {
      method: "POST",
      body: JSON.stringify({ action_id: crypto.randomUUID(), expected_version: expectedVersion }),
    }),
  disagree: (id: string, seq: number) =>
    request<void>(`/matches/${id}/turns/${seq}/disagree`, { method: "POST" }),
  stage: () => request<StageView>("/stage"),
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
