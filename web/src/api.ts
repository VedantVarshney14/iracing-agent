import type { Garage61Laps, GhostResult, LapsResponse, Review, SystemInfo, TrackRow } from "./types";

async function get<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const query = new URLSearchParams(params).toString();
  return parse<T>(await fetch(query ? `${path}?${query}` : path));
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return parse<T>(await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
}

async function parse<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({ error: `${res.status} ${res.statusText}` }));
  if (!res.ok) throw new Error(body.error ?? `${res.status} ${res.statusText}`);
  return body as T;
}

export const api = {
  tracks: () => get<TrackRow[]>("/api/tracks"),
  laps: (track: string, car: string) => get<LapsResponse>("/api/laps", { track, car }),
  review: (lap: string, ref?: string) => get<Review>("/api/review", ref ? { lap, ref } : { lap }),
  garage61Laps: (track: string, car: string) => get<Garage61Laps>("/api/garage61/laps", { track, car }),
  garage61Import: (garage61_id: string) => post<{ lap_id: string }>("/api/garage61/import", { garage61_id }),
  garage61Ghost: (garage61_id: string, install: boolean) => post<GhostResult>("/api/garage61/ghost", { garage61_id, install }),
  system: () => get<SystemInfo>("/api/system"),
};
