import { useMemo, useState } from "react";
import { extent, indexAt } from "../geometry";
import { signed } from "../format";
import type { ChannelName, Review, Series } from "../types";
import { Lanes, type Band, type Label, type Lane, type Marker } from "./Lanes";

const LANE_CHOICES: { key: string; label: string; default: boolean }[] = [
  { key: "loss", label: "Time loss", default: true },
  { key: "gap", label: "Gap", default: true },
  { key: "speed", label: "Speed", default: true },
  { key: "dv", label: "Speed diff", default: false },
  { key: "throttle", label: "Throttle", default: true },
  { key: "brake", label: "Brake", default: true },
  { key: "gear", label: "Gear", default: true },
  { key: "steer", label: "Steering", default: false },
  { key: "offset", label: "Line offset", default: false },
];
const STORE_KEY = "iagent.lanes";
const MARKERS_BELOW_M = 1500; // show brake/throttle markers once zoomed in this far

interface Props {
  review: Review;
  range: [number, number];
  cursor: number | null;
  selected: number[];
  primary: number | null;
  onCursor: (d: number | null) => void;
  onZoom: (range: [number, number] | null) => void;
  onPickCorner: (id: number) => void;
  onAsk: () => void;
}

export function Telemetry({ review, range, cursor, selected, primary, onCursor, onZoom, onPickCorner, onAsk }: Props) {
  const { trace, corners } = review;
  const dist = trace.distance_m;
  const [shown, setShown] = useState<string[]>(loadLanes);
  const toggle = (key: string) => {
    const next = shown.includes(key) ? shown.filter((k) => k !== key) : [...shown, key];
    setShown(next);
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(next));
    } catch {
      // Not critical: the choice just won't be remembered.
    }
  };

  const [r0, r1] = range;
  const span = r1 - r0;
  const lanes = useMemo<Lane[]>(() => {
    const [from, to] = [indexAt(dist, r0), indexAt(dist, r1) + 1];
    const vis = (s: Series | undefined) => (s ?? []).slice(from, to);
    const fit = (pad: number, ...s: (Series | undefined)[]): [number, number] => {
      const [lo, hi] = extent(...s.map(vis));
      const p = Math.max(pad, (hi - lo) * 0.08);
      return [lo - p, hi + p];
    };
    const sym = (min: number, ...s: (Series | undefined)[]): [number, number] => {
      const m = Math.max(min, ...s.flatMap((x) => vis(x).map((v) => (v == null ? 0 : Math.abs(v))))) * 1.15;
      return [-m, m];
    };
    const ch = (name: ChannelName): [Series, Series] => [trace.lap[name] ?? [], trace.ref[name] ?? []];
    const pct = (v: number) => `${Math.round(v)}%`;
    const [sl, sr] = ch("Speed");
    const dv = sl.map((v, i) => (v == null || sr[i] == null ? null : v - sr[i]!));

    // Time lost per bucket: where the gap grows, not just how big it is.
    const bucket = [10, 20, 50, 100].find((b) => span / b <= 160) ?? 200;
    const bars: { start: number; end: number; value: number }[] = [];
    for (let s = Math.floor(r0 / bucket) * bucket; s < r1; s += bucket) {
      const a = trace.gap_s[indexAt(dist, s)], b = trace.gap_s[indexAt(dist, s + bucket)];
      if (a != null && b != null) bars.push({ start: s, end: s + bucket, value: (b - a) * 1000 });
    }
    const barMax = Math.max(5, ...bars.map((b) => Math.abs(b.value))) * 1.1;

    const all: Lane[] = [
      { key: "loss", title: `Time lost per ${bucket} m`, height: 64, lines: [], domain: [-barMax, barMax], zero: true, bars,
        format: (v) => `${signed(v, 0)} ms`,
        readout: (i) => {
          const b = bars.find((x) => dist[i] >= x.start && dist[i] < x.end);
          return <span className={b && b.value > 0 ? "loss-text" : "gain-text"}>{b ? `${signed(b.value, 0)} ms` : "—"}</span>;
        } },
      { key: "gap", title: "Gap to ghost s", height: 72, lines: [{ values: trace.gap_s, cls: "gap" }], domain: fit(0.03, trace.gap_s),
        zero: true, fill: "zero", fillGood: "below", format: (v) => signed(v) },
      { key: "speed", title: "Speed km/h", height: 150, lines: [{ values: sl, cls: "you" }, { values: sr, cls: "ghost" }],
        domain: fit(3, sl, sr), fill: "between", format: (v) => v.toFixed(1) },
      { key: "dv", title: "Speed vs ghost km/h", height: 70, lines: [{ values: dv, cls: "gap" }], domain: sym(3, dv), zero: true,
        fill: "zero", format: (v) => signed(v, 1) },
      { key: "throttle", title: "Throttle", height: 64, lines: [{ values: ch("Throttle")[0], cls: "you" }, { values: ch("Throttle")[1], cls: "ghost" }],
        domain: [-4, 104], fill: "between", format: pct },
      { key: "brake", title: "Brake", height: 64, lines: [{ values: ch("Brake")[0], cls: "you" }, { values: ch("Brake")[1], cls: "ghost" }],
        domain: [-4, 104], fill: "between", fillGood: "neutral", format: pct },
      { key: "gear", title: "Gear", height: 52, lines: [{ values: ch("Gear")[0], cls: "you" }, { values: ch("Gear")[1], cls: "ghost" }],
        domain: [0, extent(...ch("Gear"))[1] + 0.6], format: (v) => String(Math.round(v)) },
      { key: "steer", title: "Steering °", height: 72, lines: [{ values: ch("SteeringWheelAngle")[0], cls: "you" }, { values: ch("SteeringWheelAngle")[1], cls: "ghost" }],
        domain: sym(5, ...ch("SteeringWheelAngle")), zero: true, format: (v) => `${Math.round(v)}°` },
    ];
    if (trace.offset_m) {
      all.push({ key: "offset", title: "Line vs ghost m (+ left)", height: 72, lines: [{ values: trace.offset_m, cls: "offset" }],
        domain: sym(2, trace.offset_m), zero: true, fill: "zero", fillGood: "neutral",
        format: (v) => `${Math.abs(v).toFixed(1)} ${v >= 0 ? "left" : "right"}` });
    }
    return all.filter((l) => shown.includes(l.key));
  }, [trace, dist, r0, r1, span, shown]);

  const bands: Band[] = corners
    .filter((c) => selected.includes(c.id))
    .map((c) => ({ start: c.entry_m, end: c.exit_m, strong: c.id === primary }));
  const labels: Label[] = corners.map((c) => ({ d: c.apex_m, text: `T${c.id}`, on: selected.includes(c.id), priority: c.flat ? 0 : 1 }));
  const markers: Marker[] = [];
  if (span <= MARKERS_BELOW_M) {
    for (const c of corners) {
      if (c.exit_m < r0 || c.entry_m > r1) continue;
      if (c.ref_brake_m != null) markers.push({ d: c.ref_brake_m, who: "ghost", label: `ghost brake ${Math.round(c.ref_brake_m)}`, dashed: true });
      if (c.brake_m != null) markers.push({ d: c.brake_m, who: "you", label: `brake ${Math.round(c.brake_m)}${diff(c.brake_diff_m)}`, dashed: true });
      if (c.ref_full_throttle_m != null) markers.push({ d: c.ref_full_throttle_m, who: "ghost", label: `ghost full ${Math.round(c.ref_full_throttle_m)}` });
      if (c.full_throttle_m != null) markers.push({ d: c.full_throttle_m, who: "you", label: `full ${Math.round(c.full_throttle_m)}${diff(c.full_throttle_diff_m)}` });
    }
  }
  const primaryCorner = corners.find((c) => c.id === primary);
  const zoomed = span < review.track.length_m - 1;

  return (
    <section className="card telemetry" aria-label="Telemetry">
      <div className="card-head">
        <h2>
          Telemetry <span className="muted">· drag to zoom, double-click to reset</span>
        </h2>
        <div className="row">
          <button type="button" className="btn" onClick={onAsk}>Ask coach</button>
          {primaryCorner && (
            <button type="button" className="btn" onClick={() => onZoom([primaryCorner.entry_m - 80, primaryCorner.exit_m + 80])}>
              Zoom to {primaryCorner.label}
            </button>
          )}
          {zoomed && <button type="button" className="btn" onClick={() => onZoom(null)}>Whole lap</button>}
        </div>
      </div>
      <div className="lane-chips" role="group" aria-label="Channels shown">
        {LANE_CHOICES.filter((c) => c.key !== "offset" || trace.offset_m).map((c) => (
          <button key={c.key} type="button" className="lane-chip" aria-pressed={shown.includes(c.key)} onClick={() => toggle(c.key)}>
            {c.label}
          </button>
        ))}
        <span className="muted small">Shaded blue where you're ahead of the ghost, orange where you're behind</span>
      </div>
      <Lanes
        dist={dist}
        range={range}
        lanes={lanes}
        markers={markers}
        bands={bands}
        labels={labels}
        cursor={cursor}
        probe={primaryCorner?.apex_m ?? null}
        onCursor={onCursor}
        onZoom={onZoom}
        onPick={(d) => {
          const hit = corners.find((c) => d >= c.entry_m - 30 && d <= c.exit_m + 30);
          if (hit) onPickCorner(hit.id);
        }}
      />
    </section>
  );
}

/** " (+101 m)" against the ghost, for marker labels. */
function diff(v: number | null | undefined): string {
  return v == null || v === 0 ? "" : ` (${signed(v, 0)} m)`;
}

function loadLanes(): string[] {
  try {
    const saved = localStorage.getItem(STORE_KEY);
    if (saved) return JSON.parse(saved);
  } catch {
    // Storage unavailable: use the defaults.
  }
  return LANE_CHOICES.filter((c) => c.default).map((c) => c.key);
}
