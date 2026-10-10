import type {
  Garage61Account, Garage61Laps, GhostResult, IngestResult, LapsResponse, Review, SystemInfo, TelemetryStatus, TrackRow,
} from "./types";

// Every POST carries X-Iagent: the server refuses writes without it, so other sites can't make them.
export const POST_HEADERS = { "Content-Type": "application/json", "X-Iagent": "1" };

async function get<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const query = new URLSearchParams(params).toString();
  return parse<T>(await fetch(query ? `${path}?${query}` : path));
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return parse<T>(await fetch(path, { method: "POST", headers: POST_HEADERS, body: JSON.stringify(body) }));
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
  garage61Status: () => get<Garage61Account>("/api/garage61/status"),
  garage61Token: (token: string) => post<Garage61Account>("/api/garage61/token", { token }),
  telemetryFolder: (folder: string) => post<TelemetryStatus>("/api/telemetry/folder", { folder }),
  rescan: () => post<{ ingested: string[]; telemetry: TelemetryStatus }>("/api/telemetry/rescan", {}),
  /** Upload one .ibt recording and ingest it. */
  ingest: async (file: File) =>
    parse<IngestResult>(
      await fetch(`/api/ingest?${new URLSearchParams({ name: file.name })}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Iagent": "1" },
        body: file,
      }),
    ),
};
