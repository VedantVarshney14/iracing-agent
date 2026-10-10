// Shapes returned by the `iagent ui` API (iagent/ui/review.py).

export interface TrackRow {
  track: string;
  car: string;
  track_name: string;
  car_name: string;
  laps: number;
  valid_laps: number;
  best_lap_time: number | null;
  last_session: string;
  reference_laps: number;
}

export interface LapRow {
  lap_id: string;
  session_id: string;
  seq: number;
  lap_time: number | null;
  valid: boolean;
  representative: boolean;
  reasons: string[];
  off_track_s: number;
  vs_best_pct: number | null;
}

export interface RefRow {
  lap_id: string;
  lap_time: number | null;
  valid: boolean;
  driver: string | null;
  date: string | null;
  source: string;
}

export interface LapsResponse {
  track: string;
  car: string;
  track_name: string;
  car_name: string;
  laps: LapRow[];
  references: RefRow[];
  default_lap: string | null;
  default_ghost: string | null;
}

export interface Corner {
  id: number;
  label: string;
  name: string | null;
  direction: "L" | "R";
  entry_m: number;
  apex_m: number;
  exit_m: number;
  segment_start_m: number;
  segment_end_m: number;
  flat: boolean;
  // Comparison against the ghost (absent if a lap is missing the corner).
  delta_s?: number | null;
  brake_m?: number | null;
  ref_brake_m?: number | null;
  brake_diff_m?: number | null;
  full_throttle_m?: number | null;
  ref_full_throttle_m?: number | null;
  min_speed_kph?: number | null;
  ref_min_speed_kph?: number | null;
  min_speed_diff_kph?: number | null;
  full_throttle_diff_m?: number | null;
  exit_speed_diff_kph?: number | null;
  off_track_m?: number | null;
  ref_off_track_m?: number | null;
}

export type ChannelName = "Speed" | "Throttle" | "Brake" | "Gear" | "SteeringWheelAngle";
export type Series = (number | null)[];
export type Channels = Partial<Record<ChannelName, Series>>;

export interface LapInfo {
  lap_id: string;
  session_id: string;
  seq: number;
  lap_time: number | null;
  reference: boolean;
  driver: string | null;
  garage61_id: string | null;
  ghost_available: boolean | null;
}

export interface Line {
  x: Series;
  y: Series;
}

export interface Review {
  track: { key: string; name: string; car: string; car_name: string; length_m: number };
  lap: LapInfo;
  ref: LapInfo;
  total_delta_s: number | null;
  corners: Corner[];
  trace: {
    distance_m: number[];
    gap_s: Series;
    offset_m: Series | null; // your line's distance from the ghost's, + to the left of travel
    lap: Channels;
    ref: Channels;
  };
  position: { lap: Line; ref: Line; ref_index: number[] } | null;
}

export interface Garage61Lap {
  garage61_id: string;
  driver: string | null;
  driver_slug: string | null;
  lap_time: number | null;
  date: string;
  driver_rating: number | null;
  vs_your_best_pct?: number;
  can_view_telemetry: boolean | null;
  lap_id: string | null; // set once imported as a reference lap
}

export interface Garage61Laps {
  available: boolean;
  reason?: string; // why Garage61 isn't available (no token, track not on Garage61, ...)
  laps: Garage61Lap[];
}

export interface TelemetryStatus {
  folder: string;
  found: boolean;
  watching: boolean;
  files_ingested: number;
  last: { file: string; laps?: number; track?: string | null; at: string; error?: string } | null;
  error: string | null;
  version: number; // bumped when new laps arrive
}

export interface SystemInfo {
  platform: string;
  lapfiles: string;
  lapfiles_found: boolean; // iRacing installed here: ghosts can be installed directly
  telemetry: TelemetryStatus;
  garage61: { token: boolean };
  coach: { found: boolean; command: string };
}

export interface Garage61Account {
  connected: boolean;
  reason?: string;
  user?: string;
  teams?: string[];
}

export interface IngestResult {
  file: string;
  laps: number;
  valid?: number;
  track?: string | null;
  track_key?: string | null;
  car_key?: string | null;
  skipped?: string;
}

export interface GhostResult {
  driver: string | null;
  saved: string;
  installed: string | null;
  download: string;
}

// The live coach (iagent/live/session.py).
export interface LiveFocus {
  cue: number;
  corners: number[];
  label: string;
  set_lap: number;
  done_lap: number | null;
  manual: boolean;
}

export interface LiveStatus {
  state: "idle" | "starting" | "waiting" | "running" | "stopping" | "error";
  error: string | null;
  session_id: string | null;
  options: { source: string; file: string | null; speed: number; voice: string; learning_laps: number; focus: boolean } | null;
  events: number;
  track?: { key: string; name: string; car: string; car_name: string };
  ref?: { lap_id: string; lap_time: number | null; driver?: string | null };
  laps_done?: number;
  mode?: "pushing" | "tranquille";
  learning?: boolean;
  focus?: LiveFocus | null;
  cues?: { corners: number[]; text: string; cued: boolean }[];
  lap_dist?: number | null;
  session_time?: number | null;
}

export type LiveEvent = { seq: number; wall: string; at?: number | null } & (
  | { type: "status"; state: string; track?: string; car?: string; ref?: string }
  | { type: "line"; status: "said" | "cut" | "dropped"; kind: string; text: string; corner: number | null; note: string | null }
  | { type: "lap"; lap: number; lap_time: number | null; gap_s: number | null; pace: "pushing" | "moment" | "tranquille";
      pushing_share: number; slow: [number, number][]; moment_at: number | null; corners: { corner: number; delta_s: number }[]; focus: number | null }
  | { type: "pace"; mode: "pushing" | "tranquille"; lap_dist: number }
  | { type: "advice"; corner: number; lap: number; delta_s: number; advice: string | null; hint: string | null }
  | { type: "focus"; focus: LiveFocus | null; by: string }
  | { type: "driver"; text: string }
  | { type: "answer"; text: string }
  | { type: "error"; message: string }
);

export interface Recording {
  path: string;
  name: string;
  size: number;
  modified: string;
}

// A coached session, afterwards (iagent/live/report.py).
export type Pace = "pushing" | "moment" | "tranquille";
export type Verdict = "working" | "mixed" | "not yet" | "no laps since" | "sorted" | "replaced";

export interface FocusOutcome extends LiveFocus {
  carried?: boolean;
  replaced?: boolean;
  before_s: number | null;
  after: { lap: number; loss_s: number }[];
  change_s: number | null;
  verdict: Verdict;
}

export interface SessionRow {
  id: string;
  track: string | null;
  track_key: string | null;
  car: string | null;
  car_key: string | null;
  started: string;
  source: string | null;
  laps: number;
  best_lap: number | null;
  ref: string | null;
  focus: FocusOutcome | null;
}

export interface SessionLap {
  lap: number;
  lap_time: number | null;
  gap_s: number | null;
  pace: Pace;
  moment_at: number | null;
  slow: [number, number][];
  pushing_share: number | null;
  focus: number | null;
  said: number;
  held_back: number;
  lap_id: string | null;
}

export interface AdviceOutcome {
  corner: number;
  lap: number;
  advice: string | null;
  hint: string | null;
  before_s: number;
  heard: string | null;
  times: number;
  after: { lap: number; loss_s: number }[];
  verdict: Verdict;
}

export interface SessionReport {
  id: string;
  started: string | null;
  ended: boolean;
  track: { key: string | null; name: string | null; car: string | null; car_name: string | null; length_m: number | null;
    corners: { id: number; name: string | null; apex_m: number }[] };
  ref: { lap_id: string | null; lap_time: number | null; driver: string | null };
  source: string | null;
  duration_s: number | null;
  summary: { laps: number; pushing: number; moments: number; tranquille: number; best_lap: number | null; best_lap_no: number | null;
    best_gap_s: number | null; said: number; cut: number; held_back: number; held_reasons: Record<string, number> };
  laps: SessionLap[];
  corners: { corner: number; mean_s: number; laps: number }[];
  focus: FocusOutcome[];
  advice: AdviceOutcome[];
  commentary: { lap: string; events: LiveEvent[] }[];
  map: { x: (number | null)[]; y: (number | null)[]; corners: { id: number; name: string | null; from: number; to: number; apex: number }[] } | null;
  debrief: string | null;
  debrief_running: boolean;
  next_plan: { focus: number; note: string; from_session: string | null; sessions_left: number } | null;
  cues: { corners: number[]; text: string; source: string }[];
  context: string[];
}
