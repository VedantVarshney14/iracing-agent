import { useMemo, useState } from "react";
import { extent, indexAt } from "../geometry";
import { deltaColor, metres, signed } from "../format";
import type { Corner, Review, Series } from "../types";
import { CornerLines } from "./CornerLines";
import { Lanes, type Lane, type Marker } from "./Lanes";

const SCALES = [1, 3, 5, 10];

interface Props {
  review: Review;
  corner: Corner;
  window: [number, number];
  cursor: number | null;
  onCursor: (d: number | null) => void;
  onCorner: (id: number) => void;
  onBack: () => void;
  onAsk: (text: string) => void;
}

export function CornerView({ review, corner, window, cursor, onCursor, onCorner, onBack, onAsk }: Props) {
  const [scale, setScale] = useState(5);
  const [ladder, setLadder] = useState(true);
  const { corners, trace } = review;
  const dist = trace.distance_m;
  const index = corners.findIndex((c) => c.id === corner.id);
  const prev = corners[index - 1];
  const next = corners[index + 1];
  const ghostName = review.ref.driver ?? "the ghost";

  // Line offset as the driver reads it: + wider (towards the outside of this corner).
  const wider = useMemo<Series>(() => {
    const sign = corner.direction === "L" ? -1 : 1; // outside of a left-hander is to the right
    return (trace.offset_m ?? []).map((v) => (v == null ? null : v * sign));
  }, [trace.offset_m, corner.direction]);

  const widest = useMemo(() => {
    let best: { m: number; d: number } | null = null;
    for (let i = indexAt(dist, corner.entry_m); i <= indexAt(dist, corner.exit_m); i++) {
      const v = wider[i];
      if (v != null && (!best || Math.abs(v) > Math.abs(best.m))) best = { m: v, d: dist[i] };
    }
    return best;
  }, [wider, dist, corner]);

  const lanes = useMemo<Lane[]>(() => {
    const [from, to] = [indexAt(dist, window[0]), indexAt(dist, window[1]) + 1];
    const vis = (s: Series | undefined) => (s ?? []).slice(from, to);
    const fit = (pad: number, ...s: (Series | undefined)[]): [number, number] => {
      const [lo, hi] = extent(...s.map(vis));
      const p = Math.max(pad, (hi - lo) * 0.1);
      return [lo - p, hi + p];
    };
    const sym = (min: number, ...s: (Series | undefined)[]): [number, number] => {
      const m = Math.max(min, ...s.flatMap((x) => vis(x).map((v) => (v == null ? 0 : Math.abs(v))))) * 1.15;
      return [-m, m];
    };
    const ch = (name: "Speed" | "Throttle" | "Brake" | "SteeringWheelAngle") => [trace.lap[name] ?? [], trace.ref[name] ?? []];
    const [sl, sr] = ch("Speed");
    const g0 = trace.gap_s[from] ?? 0;
    const gap = trace.gap_s.map((v) => (v == null ? null : v - g0)); // time vs ghost since the window start
    const dv = sl.map((v, i) => (v == null || sr[i] == null ? null : v - sr[i]!));
    const pct = (v: number) => `${Math.round(v)}%`;
    const out: Lane[] = [
      { key: "gap", title: `Time vs ghost from ${metres(window[0])}`, height: 70, lines: [{ values: gap, cls: "gap" }],
        domain: fit(0.02, gap), zero: true, fill: "zero", fillGood: "below", format: (v) => `${signed(v)} s` },
      { key: "speed", title: "Speed km/h", height: 130, lines: [{ values: sl, cls: "you" }, { values: sr, cls: "ghost" }],
        domain: fit(2, sl, sr), fill: "between", format: (v) => v.toFixed(1) },
      { key: "dv", title: "Speed vs ghost km/h", height: 70, lines: [{ values: dv, cls: "gap" }],
        domain: sym(3, dv), zero: true, fill: "zero", format: (v) => signed(v, 1) },
      { key: "thr", title: "Throttle", height: 64, lines: [{ values: ch("Throttle")[0], cls: "you" }, { values: ch("Throttle")[1], cls: "ghost" }],
        domain: [-4, 104], fill: "between", format: pct },
      { key: "brk", title: "Brake", height: 56, lines: [{ values: ch("Brake")[0], cls: "you" }, { values: ch("Brake")[1], cls: "ghost" }],
        domain: [-4, 104], fill: "between", fillGood: "neutral", format: pct },
      { key: "steer", title: "Steering °", height: 64, lines: [{ values: ch("SteeringWheelAngle")[0], cls: "you" }, { values: ch("SteeringWheelAngle")[1], cls: "ghost" }],
        domain: sym(5, ...ch("SteeringWheelAngle")), zero: true, format: (v) => `${Math.round(v)}°` },
    ];
    if (trace.offset_m) {
      out.push({ key: "offset", title: "Line vs ghost m (wider ↑)", height: 90, lines: [{ values: wider, cls: "offset" }],
        domain: sym(2, wider), zero: true, fill: "zero", fillGood: "neutral", format: (v) => `${v > 0 ? "wider" : "tighter"} ${Math.abs(v).toFixed(1)}` });
    }
    return out;
  }, [trace, dist, window, wider]);

  const markers: Marker[] = [];
  for (const c of corners) {
    if (c.apex_m < window[0] - 150 || c.apex_m > window[1] + 150) continue;
    if (c.ref_brake_m != null) markers.push({ d: c.ref_brake_m, who: "ghost", label: `ghost brake ${Math.round(c.ref_brake_m)}`, dashed: true });
    if (c.brake_m != null) markers.push({ d: c.brake_m, who: "you", label: `brake ${Math.round(c.brake_m)}`, dashed: true });
    if (c.ref_full_throttle_m != null) markers.push({ d: c.ref_full_throttle_m, who: "ghost", label: `ghost full ${Math.round(c.ref_full_throttle_m)}` });
    if (c.full_throttle_m != null) markers.push({ d: c.full_throttle_m, who: "you", label: `full ${Math.round(c.full_throttle_m)}` });
  }

  const brake = c2(corner.brake_diff_m, (v) => `${Math.abs(v).toFixed(0)} m ${v > 0 ? "later" : "earlier"}`, corner.brake_m == null && corner.ref_brake_m == null ? "no braking" : "—");
  const full = c2(corner.full_throttle_diff_m, (v) => `${Math.abs(v).toFixed(0)} m ${v > 0 ? "later" : "earlier"}`, "—");

  return (
    <main className="layout corner-page">
      <div className="corner-head">
        <button type="button" className="btn" onClick={onBack}>← Whole lap</button>
        <button type="button" className="btn icon" aria-label={prev ? `Previous corner, ${prev.label}` : "No previous corner"} disabled={!prev} onClick={() => prev && onCorner(prev.id)}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M15 6l-6 6 6 6" /></svg>
        </button>
        <label className="field">
          <span className="sr-only">Corner</span>
          <select value={corner.id} onChange={(e) => onCorner(Number(e.target.value))}>
            {corners.map((c) => (
              <option key={c.id} value={c.id}>{c.label} · {signed(c.delta_s)} s</option>
            ))}
          </select>
        </label>
        <button type="button" className="btn icon" aria-label={next ? `Next corner, ${next.label}` : "No next corner"} disabled={!next} onClick={() => next && onCorner(next.id)}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6l6 6-6 6" /></svg>
        </button>
        <div className="corner-title">
          <h1>{corner.label}</h1>
          <span className="muted">
            {corner.direction === "L" ? "Left" : "Right"}{corner.flat ? ", flat" : ""} · {metres(corner.entry_m)} – {metres(corner.exit_m)} · vs {ghostName}
          </span>
        </div>
        <div className="delta-pill" style={{ borderColor: deltaColor(corner.delta_s) }}>
          <span className="mono" style={{ color: deltaColor(corner.delta_s) }}>{signed(corner.delta_s)} s</span>
          <span>in this corner</span>
        </div>
        <button type="button" className="btn" onClick={() => onAsk(`What am I doing differently from ${ghostName} in ${corner.label}, and what should I change?`)}>
          Ask coach
        </button>
      </div>

      <div className="corner-body">
        <section className="card lines-card" aria-label="Racing lines">
          <div className="card-head">
            <h2>Racing lines</h2>
            <div className="row">
              <div className="segmented" role="group" aria-label="Sideways scale">
                {SCALES.map((s) => (
                  <button key={s} type="button" aria-pressed={scale === s} onClick={() => setScale(s)}>
                    {s === 1 ? "True scale" : `×${s}`}
                  </button>
                ))}
              </div>
              <button type="button" className="btn" aria-pressed={ladder} onClick={() => setLadder(!ladder)}>
                Gap ladder {ladder ? "on" : "off"}
              </button>
            </div>
          </div>
          {review.position ? (
            <CornerLines review={review} corner={corner} window={window} scale={scale} ladder={ladder} cursor={cursor} />
          ) : (
            <p className="muted empty">No position data for these laps. Re-run <code>iagent ingest</code> on the recording.</p>
          )}
        </section>

        <div className="corner-side">
          <section className="metrics" aria-label="Corner numbers">
            <Metric label="Time" value={`${signed(corner.delta_s)} s`} color={deltaColor(corner.delta_s)} note={`vs ${ghostName}`} />
            <Metric label="Minimum speed" value={`${corner.min_speed_kph?.toFixed(1) ?? "—"}`} note={`${corner.ref_min_speed_kph?.toFixed(1) ?? "—"} ghost · ${signed(corner.min_speed_diff_kph, 1)} km/h`} />
            <Metric label="Exit speed" value={`${signed(corner.exit_speed_diff_kph, 1)} km/h`} color={tone(corner.exit_speed_diff_kph)} note="vs ghost" />
            <Metric label="Braking" value={brake} note="vs ghost" />
            <Metric label="Full throttle" value={full} color={tone(corner.full_throttle_diff_m == null ? null : -corner.full_throttle_diff_m)} note="held 20 m" />
            <Metric
              label="Widest line gap"
              value={widest ? `${Math.abs(widest.m).toFixed(1)} m` : "—"}
              note={widest ? `${widest.m > 0 ? "wider" : "tighter"} at ${metres(widest.d)}` : "no position data"}
            />
          </section>
          {((corner.off_track_m ?? 0) > 0 || (corner.ref_off_track_m ?? 0) > 0) && (
            <p className="warn small">
              Off track here: you {corner.off_track_m ?? 0} m, ghost {corner.ref_off_track_m ?? 0} m. Treat this corner's numbers as an incident.
            </p>
          )}
        </div>
      </div>

      <section className="card" aria-label="Corner telemetry">
        <div className="card-head">
          <h2>Telemetry <span className="muted">· {metres(window[0])} – {metres(window[1])}, shaded where you're ahead (blue) or behind (orange)</span></h2>
        </div>
        <Lanes dist={dist} range={window} lanes={lanes} markers={markers} bands={[{ start: corner.entry_m, end: corner.exit_m }]} cursor={cursor} onCursor={onCursor} />
      </section>
    </main>
  );
}

function Metric({ label, value, note, color }: { label: string; value: string; note: string; color?: string }) {
  return (
    <div className="metric">
      <span className="muted small">{label}</span>
      <span className="mono metric-value" style={color ? { color } : undefined}>{value}</span>
      <span className="muted small">{note}</span>
    </div>
  );
}

function c2(v: number | null | undefined, f: (v: number) => string, missing: string): string {
  return v == null ? missing : v === 0 ? "same point" : f(v);
}

/** Positive is good for the driver (blue), negative bad (orange). */
function tone(v: number | null | undefined): string | undefined {
  if (v == null || Math.abs(v) < 0.5) return undefined;
  return v > 0 ? "var(--gain-strong)" : "var(--loss-strong)";
}
