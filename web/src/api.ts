import type { LapsResponse, Review, TrackRow } from "./types";

async function get<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const query = new URLSearchParams(params).toString();
  const res = await fetch(query ? `${path}?${query}` : path);
  const body = await res.json().catch(() => ({ error: `${res.status} ${res.statusText}` }));
  if (!res.ok) throw new Error(body.error ?? `${res.status} ${res.statusText}`);
  return body as T;
}

export const api = {
  tracks: () => get<TrackRow[]>("/api/tracks"),
  laps: (track: string, car: string) => get<LapsResponse>("/api/laps", { track, car }),
  review: (lap: string, ref?: string) => get<Review>("/api/review", ref ? { lap, ref } : { lap }),
};
