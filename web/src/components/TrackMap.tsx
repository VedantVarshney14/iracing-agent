import { useMemo, useRef } from "react";
import { deltaColor, metres, signed } from "../format";
import { indexAt, polyline, useWidth } from "../geometry";
import type { Corner, Review } from "../types";

const ASPECT = 3 / 2; // width / height of the map box

interface Props {
  review: Review;
  mode: "lap" | "corner";
  window: [number, number] | null; // distance range shown in corner mode
  selected: number[];
  primary: number | null;
  cursor: number | null;
  onMode: (mode: "lap" | "corner") => void;
  onPickCorner: (id: number) => void;
  onOpenCorner: (id: number) => void;
}

export function TrackMap({ review, mode, window, selected, primary, cursor, onMode, onPickCorner, onOpenCorner }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const width = useWidth(boxRef);
  const pos = review.position;
  const dist = review.trace.distance_m;
  const corners = review.corners;
  const primaryCorner = corners.find((c) => c.id === primary) ?? null;
  const cornerMode = mode === "corner" && window != null;

  // Screen coordinates: x east, y down (north up).
  const lines = useMemo(() => {
    if (!pos) return null;
    const flip = (ys: (number | null)[]) => ys.map((v) => (v == null ? null : -v));
    return { lap: { x: pos.lap.x, y: flip(pos.lap.y) }, ref: { x: pos.ref.x, y: flip(pos.ref.y) } };
  }, [pos]);

  const view = useMemo(() => {
    if (!lines) return null;
    const [from, to] = cornerMode ? window!.map((d) => indexAt(dist, d)) : [0, dist.length - 1];
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const line of [lines.lap, lines.ref]) {
      for (let i = from; i <= to; i++) {
        const x = line.x[i], y = line.y[i];
        if (x == null || y == null) continue;
        x0 = Math.min(x0, x); x1 = Math.max(x1, x);
        y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
    }
    const pad = Math.max(x1 - x0, y1 - y0) * (cornerMode ? 0.12 : 0.06) + 10;
    let w = x1 - x0 + 2 * pad, h = y1 - y0 + 2 * pad;
    if (w / h < ASPECT) w = h * ASPECT; else h = w / ASPECT;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return { x: cx - w / 2, y: cy - h / 2, w, h, from, to, centre: [cx, cy] as const };
  }, [lines, cornerMode, window, dist]);

  if (!pos || !lines || !view) {
    return (
      <section className="card map" aria-label="Track map">
        <div className="card-head"><h2>Track map</h2></div>
        <p className="muted empty">
          These laps have no position data. Re-run <code>iagent ingest</code> on the recording to add it.
        </p>
      </section>
    );
  }

  const u = width ? view.w / width : 1; // map units per screen pixel
  const seg = (c: Corner) => [indexAt(dist, c.segment_start_m), indexAt(dist, c.segment_end_m) + 1] as const;
  const at = (line: typeof lines.lap, d: number) => {
    const i = indexAt(dist, d);
    return line.x[i] == null || line.y[i] == null ? null : ([line.x[i]!, line.y[i]!] as const);
  };
  const cursorLap = cursor == null ? null : at(lines.lap, cursor);
  const cursorRef = cursor == null ? null : at(lines.ref, cursor);
  const lineGap = cursorLap && cursorRef ? Math.hypot(cursorLap[0] - cursorRef[0], cursorLap[1] - cursorRef[1]) : null;

  return (
    <section className="card map" aria-label="Track map">
      <div className="card-head">
        <h2>
          Track map <span className="muted">· {cornerMode ? "your line vs the ghost" : "coloured by time vs ghost"}</span>
        </h2>
        <div className="segmented" role="group" aria-label="Map view">
          <button type="button" aria-pressed={!cornerMode} onClick={() => onMode("lap")}>Whole lap</button>
          <button type="button" aria-pressed={cornerMode} disabled={window == null} onClick={() => onMode("corner")}>
            Corner
          </button>
        </div>
      </div>

      <div className="map-box" ref={boxRef}>
        <svg viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`} role="img" aria-label={`Map of ${review.track.name}`}>
          {cornerMode ? (
            <>
              <path d={polyline(lines.ref.x, lines.ref.y, Math.max(0, view.from - 150), view.to + 150)} className="track-base" vectorEffect="non-scaling-stroke" />
              <path d={polyline(lines.ref.x, lines.ref.y)} className="line ghost" vectorEffect="non-scaling-stroke" />
              <path d={polyline(lines.lap.x, lines.lap.y)} className="line you" vectorEffect="non-scaling-stroke" />
              {corners
                .filter((c) => c.apex_m >= window![0] - 100 && c.apex_m <= window![1] + 100)
                .flatMap((c) => markers(c))
                .map((m) => {
                  const p = at(m.ref ? lines.ref : lines.lap, m.d);
                  if (!p) return null;
                  return (
                    <g key={m.key} className={`marker ${m.ref ? "ghost" : "you"} ${m.kind}`}>
                      <circle cx={p[0]} cy={p[1]} r={5 * u} />
                      <text x={p[0] + (m.ref ? -10 : 10) * u} y={p[1] + (m.ref ? 14 : -8) * u} fontSize={11 * u} textAnchor={m.ref ? "end" : "start"}>
                        {m.kind === "brake" ? "brake" : "full"} {Math.round(m.d)}
                      </text>
                    </g>
                  );
                })}
              <ScaleBar x={view.x + 16 * u} y={view.y + view.h - 16 * u} u={u} span={view.w} />
            </>
          ) : (
            <>
              <path d={polyline(lines.ref.x, lines.ref.y)} className="track-base" vectorEffect="non-scaling-stroke" />
              {corners
                .filter((c) => selected.includes(c.id))
                .map((c) => (
                  <path key={c.id} d={polyline(lines.lap.x, lines.lap.y, ...seg(c))} className="halo" vectorEffect="non-scaling-stroke" />
                ))}
              {corners.map((c) => (
                <g key={c.id} onClick={() => onPickCorner(c.id)} className="seg">
                  <path d={polyline(lines.lap.x, lines.lap.y, ...seg(c))} className="hit" vectorEffect="non-scaling-stroke" />
                  <path
                    d={polyline(lines.lap.x, lines.lap.y, ...seg(c))}
                    className={(c.ref_off_track_m ?? 0) > 0 ? "delta dashed" : "delta"}
                    style={{ stroke: deltaColor(c.delta_s) }}
                    vectorEffect="non-scaling-stroke"
                  >
                    <title>{`${c.label}: ${signed(c.delta_s)} s`}</title>
                  </path>
                </g>
              ))}
              {placeLabels(corners, (d) => at(lines.ref, d), lines.ref, view.centre, u).map(({ c, x, y }) => (
                <text
                  key={c.id}
                  x={x}
                  y={y}
                  fontSize={11 * u}
                  textAnchor="middle"
                  className={selected.includes(c.id) ? "corner-label on" : "corner-label"}
                >
                  T{c.id}
                </text>
              ))}
            </>
          )}
          {cursorRef && <circle cx={cursorRef[0]} cy={cursorRef[1]} r={5 * u} className="dot ghost" />}
          {cursorLap && <circle cx={cursorLap[0]} cy={cursorLap[1]} r={5 * u} className="dot you" />}
        </svg>

        {primaryCorner && (
          <CornerCard corner={primaryCorner} lineGap={cornerMode ? lineGap : null} onOpen={() => onOpenCorner(primaryCorner.id)} />
        )}
      </div>
      <p className="legend">
        {cornerMode ? (
          <>
            <span className="swatch you" /> You <span className="swatch ghost dashed" /> Ghost · hover the traces to follow both cars
          </>
        ) : (
          <>
            gain <i style={{ background: "var(--gain-strong)" }} /><i style={{ background: "var(--gain)" }} />
            <i style={{ background: "var(--neutral)" }} /><i style={{ background: "var(--loss)" }} />
            <i style={{ background: "var(--loss-strong)" }} /> loss · dashed: the ghost went off track there
          </>
        )}
      </p>
    </section>
  );
}

/** Corner labels beside the track at each apex: the first spot either side (outside first)
 * that clears the track and the labels already placed. Corners with a real speed drop go first;
 * a label with no room is dropped. */
function placeLabels(
  corners: Corner[],
  at: (d: number) => readonly [number, number] | null,
  track: { x: (number | null)[]; y: (number | null)[] },
  centre: readonly [number, number],
  u: number,
) {
  const pts: [number, number][] = [];
  for (let i = 0; i < track.x.length; i += 5) {
    const x = track.x[i], y = track.y[i];
    if (x != null && y != null) pts.push([x, y]);
  }
  const clear = (x: number, y: number) => pts.every(([px, py]) => Math.hypot(px - x, py - y) > 11 * u);
  const placed: { c: Corner; x: number; y: number }[] = [];
  for (const c of [...corners.filter((c) => !c.flat), ...corners.filter((c) => c.flat)]) {
    const p = at(c.apex_m), before = at(c.apex_m - 10), after = at(c.apex_m + 10);
    if (!p || !before || !after) continue;
    let nx = -(after[1] - before[1]), ny = after[0] - before[0];
    const len = Math.hypot(nx, ny) || 1;
    nx /= len; ny /= len;
    if (nx * (p[0] - centre[0]) + ny * (p[1] - centre[1]) < 0) { nx = -nx; ny = -ny; }
    spots: for (const dist of [16, 24, 34]) {
      for (const side of [1, -1]) {
        const x = p[0] + side * nx * dist * u, y = p[1] + side * ny * dist * u;
        if (clear(x, y) && placed.every((q) => Math.abs(q.x - x) > 24 * u || Math.abs(q.y - y) > 13 * u)) {
          placed.push({ c, x, y: y + 4 * u });
          break spots;
        }
      }
    }
  }
  return placed.sort((a, b) => a.c.id - b.c.id);
}

function markers(c: Corner) {
  const out: { key: string; d: number; ref: boolean; kind: "brake" | "full" }[] = [];
  if (c.brake_m != null) out.push({ key: `${c.id}-b`, d: c.brake_m, ref: false, kind: "brake" });
  if (c.ref_brake_m != null) out.push({ key: `${c.id}-rb`, d: c.ref_brake_m, ref: true, kind: "brake" });
  if (c.full_throttle_m != null) out.push({ key: `${c.id}-t`, d: c.full_throttle_m, ref: false, kind: "full" });
  if (c.ref_full_throttle_m != null) out.push({ key: `${c.id}-rt`, d: c.ref_full_throttle_m, ref: true, kind: "full" });
  return out;
}

function ScaleBar({ x, y, u, span }: { x: number; y: number; u: number; span: number }) {
  const len = [10, 20, 50, 100, 200, 500, 1000].find((m) => m > span / 8) ?? 1000;
  return (
    <g className="scale">
      <line x1={x} x2={x + len} y1={y} y2={y} vectorEffect="non-scaling-stroke" />
      <text x={x + len + 6 * u} y={y + 4 * u} fontSize={11 * u}>{len} m</text>
    </g>
  );
}

function CornerCard({ corner: c, lineGap, onOpen }: { corner: Corner; lineGap: number | null; onOpen: () => void }) {
  const off = c.ref_off_track_m ?? 0;
  return (
    <div className="corner-card">
      <div className="row between">
        <b>{c.label}</b>
        <span className="mono" style={{ color: deltaColor(c.delta_s) }}>{signed(c.delta_s)} s</span>
      </div>
      <span className="muted">{metres(c.entry_m)} – {metres(c.exit_m)} · {c.direction === "L" ? "left" : "right"}{c.flat ? ", flat" : ""}</span>
      <span className="muted">
        min {signed(c.min_speed_diff_kph, 1)} · exit {signed(c.exit_speed_diff_kph, 1)} km/h
      </span>
      {lineGap != null && <span className="muted">line gap here {lineGap.toFixed(1)} m</span>}
      {off > 0 && <span className="warn">Ghost ran {Math.round(off)} m off track here</span>}
      <button type="button" className="btn open-corner" onClick={onOpen}>Open corner view →</button>
    </div>
  );
}
