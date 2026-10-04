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
}

export interface GhostResult {
  driver: string | null;
  saved: string;
  installed: string | null;
  download: string;
}
