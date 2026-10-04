import { useMemo, useRef } from "react";
import { indexAt, polyline, useWidth } from "../geometry";
import type { Corner, Review } from "../types";

const ASPECT = 4 / 3;
const LADDER_EVERY_M = 25;

// Your line is coloured by speed against the ghost at the same point (km/h).
const SPEED_BANDS: { max: number; cls: string; label: string }[] = [
  { max: -4, cls: "loss-strong", label: "4+ slower" },
  { max: -1, cls: "loss", label: "1–4 slower" },
  { max: 1, cls: "neutral", label: "±1" },
  { max: 4, cls: "gain", label: "1–4 faster" },
  { max: Infinity, cls: "gain-strong", label: "4+ faster" },
];

interface Props {
  review: Review;
  corner: Corner;
  window: [number, number];
  scale: number; // sideways exaggeration: 1 = true scale
  ladder: boolean;
  cursor: number | null;
}

type Pt = [number, number];

export function CornerLines({ review, corner, window, scale, ladder, cursor }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const width = useWidth(boxRef);
  const pos = review.position!;
  const dist = review.trace.distance_m;
  const offset = review.trace.offset_m ?? [];
  const step = dist[1] - dist[0];

  const geo = useMemo(() => {
    const [from, to] = [indexAt(dist, window[0]), indexAt(dist, window[1])];
    const at = (line: typeof pos.lap, i: number): Pt | null =>
      line.x[i] == null || line.y[i] == null ? null : [line.x[i]!, -line.y[i]!]; // north up
    const ghost = (i: number) => at(pos.ref, i);
    // Your line, pushed `scale` times further from the ghost point it is nearest to.
    const yours = (i: number): Pt | null => {
      const p = at(pos.lap, i);
      const j = pos.ref_index[i];
      const g = j >= 0 ? ghost(j) : null;
      if (!p || !g) return p;
      return [g[0] + (p[0] - g[0]) * scale, g[1] + (p[1] - g[1]) * scale];
    };

    const pts: Pt[] = [];
    for (let i = from; i <= to; i++) for (const p of [ghost(i), yours(i)]) if (p) pts.push(p);
    let [x0, x1, y0, y1] = [Infinity, -Infinity, Infinity, -Infinity];
    for (const [x, y] of pts) {
      x0 = Math.min(x0, x); x1 = Math.max(x1, x);
      y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    const pad = Math.max(x1 - x0, y1 - y0) * 0.1 + 8;
    let w = x1 - x0 + 2 * pad, h = y1 - y0 + 2 * pad;
    if (w / h < ASPECT) w = h * ASPECT; else h = w / ASPECT;
    const centre: Pt = [(x0 + x1) / 2, (y0 + y1) / 2];
    const view = { x: centre[0] - w / 2, y: centre[1] - h / 2, w, h };

    // Your line in runs of one speed band.
    const speedLap = review.trace.lap.Speed ?? [];
    const speedRef = review.trace.ref.Speed ?? [];
    const runs: { cls: string; d: string }[] = [];
    let run: Pt[] = [];
    let cls = "";
    for (let i = from; i <= to; i++) {
      const p = yours(i);
      const a = speedLap[i], b = speedRef[i];
      const c = a == null || b == null ? "neutral" : SPEED_BANDS.find((s) => a - b <= s.max)!.cls;
      if (!p) continue;
      if (c !== cls && run.length) {
        run.push(p);
        runs.push({ cls, d: "M" + run.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join("L") });
        run = [];
      }
      cls = c;
      run.push(p);
    }
    if (run.length > 1) runs.push({ cls, d: "M" + run.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join("L") });

    // Gap ladder: a rung every 25 m where the lines are apart; label the widest and other big ones.
    const every = Math.max(1, Math.round(LADDER_EVERY_M / step));
    const rungs: { a: Pt; b: Pt; m: number; i: number }[] = [];
    for (let i = from; i <= to; i += every) {
      const o = offset[i];
      const a = yours(i), b = pos.ref_index[i] >= 0 ? ghost(pos.ref_index[i]) : null;
      if (o != null && a && b && Math.abs(o) >= 0.75) rungs.push({ a, b, m: Math.abs(o), i });
    }
    const widest = Math.max(0, ...rungs.map((r) => r.m));
    const labelled: typeof rungs = [];
    for (const r of [...rungs].sort((p, q) => q.m - p.m)) {
      if (r.m >= Math.max(1, widest * 0.5) && labelled.every((l) => Math.abs(l.i - r.i) * step >= 70)) labelled.push(r);
    }

    // Distance ticks along the ghost's line, labelled toward the inside of the corner.
    const ticks: { p: Pt; label: string; dx: number; dy: number }[] = [];
    for (let d = Math.ceil(window[0] / 100) * 100; d <= window[1]; d += 100) {
      const p = ghost(indexAt(dist, d));
      if (!p) continue;
      const vx = centre[0] - p[0], vy = centre[1] - p[1], len = Math.hypot(vx, vy) || 1;
      ticks.push({ p, label: d.toLocaleString("en-US"), dx: vx / len, dy: vy / len });
    }

    return {
      view,
      ghostPath: polyline(pos.ref.x, pos.ref.y.map((v) => (v == null ? null : -v)), Math.max(0, from - 60), Math.min(dist.length, to + 60)),
      runs,
      rungs,
      labelled,
      ticks,
      ghost,
      yours,
    };
  }, [review, window, scale, dist, offset, pos, step]);

  const u = width ? geo.view.w / width : 1; // map units per screen pixel
  const marks = [
    { d: corner.ref_brake_m, who: "ghost", kind: "brake" },
    { d: corner.brake_m, who: "you", kind: "brake" },
    { d: corner.ref_full_throttle_m, who: "ghost", kind: "full" },
    { d: corner.full_throttle_m, who: "you", kind: "full" },
  ].filter((m) => m.d != null && m.d >= window[0] && m.d <= window[1]);
  const ci = cursor == null ? null : indexAt(dist, cursor);
  const scaleLen = [10, 20, 50, 100, 200].find((m) => m > geo.view.w / 7) ?? 200;

  return (
    <div className="corner-lines" ref={boxRef}>
      <svg viewBox={`${geo.view.x} ${geo.view.y} ${geo.view.w} ${geo.view.h}`} role="img"
        aria-label={`Your line and the ghost's through ${corner.label}${scale > 1 ? `, sideways gap shown ${scale} times larger` : ""}`}>
        <path d={geo.ghostPath} className="track-base" vectorEffect="non-scaling-stroke" />
        {ladder &&
          geo.rungs.map((r, k) => (
            <line key={k} x1={r.a[0]} y1={r.a[1]} x2={r.b[0]} y2={r.b[1]} className="rung" vectorEffect="non-scaling-stroke" />
          ))}
        <path d={geo.ghostPath} className="line ghost" vectorEffect="non-scaling-stroke" />
        {geo.runs.map((r, k) => (
          <path key={k} d={r.d} className={`line speed ${r.cls}`} vectorEffect="non-scaling-stroke" />
        ))}
        {ladder &&
          geo.labelled.map((r, k) => {
            const mx = (r.a[0] + r.b[0]) / 2, my = (r.a[1] + r.b[1]) / 2;
            return (
              <text key={k} x={mx + 8 * u} y={my + 4 * u} fontSize={11 * u} className="rung-label">
                {r.m.toFixed(1)} m
              </text>
            );
          })}
        {geo.ticks.map((t) => (
          <g key={t.label} className="tick">
            <circle cx={t.p[0]} cy={t.p[1]} r={2 * u} />
            <text x={t.p[0] + t.dx * 18 * u} y={t.p[1] + t.dy * 18 * u + 4 * u} fontSize={10 * u} textAnchor="middle">{t.label}</text>
          </g>
        ))}
        {marks.map((m) => {
          const i = indexAt(dist, m.d!);
          const p = m.who === "ghost" ? geo.ghost(i) : geo.yours(i);
          if (!p) return null;
          return (
            <g key={`${m.who}-${m.kind}`} className={`marker ${m.who} ${m.kind}`}>
              <circle cx={p[0]} cy={p[1]} r={5 * u} />
              <text x={p[0] + (m.who === "ghost" ? -10 : 10) * u} y={p[1] + (m.who === "ghost" ? 14 : -8) * u}
                fontSize={11 * u} textAnchor={m.who === "ghost" ? "end" : "start"}>
                {m.who === "ghost" ? "ghost " : ""}{m.kind === "brake" ? "brake" : "full"} {Math.round(m.d!).toLocaleString("en-US")}
              </text>
            </g>
          );
        })}
        {ci != null && geo.ghost(ci) && <circle cx={geo.ghost(ci)![0]} cy={geo.ghost(ci)![1]} r={5 * u} className="dot ghost" />}
        {ci != null && geo.yours(ci) && <circle cx={geo.yours(ci)![0]} cy={geo.yours(ci)![1]} r={5 * u} className="dot you" />}
        <g className="scale">
          <line x1={geo.view.x + 16 * u} x2={geo.view.x + 16 * u + scaleLen} y1={geo.view.y + geo.view.h - 16 * u} y2={geo.view.y + geo.view.h - 16 * u} vectorEffect="non-scaling-stroke" />
          <text x={geo.view.x + 22 * u + scaleLen} y={geo.view.y + geo.view.h - 12 * u} fontSize={11 * u}>
            {scaleLen} m{scale > 1 ? " along the track" : ""}
          </text>
        </g>
      </svg>
      <div className="legend">
        {scale > 1 && <span className="scale-note">Sideways gap shown ×{scale}, distance along the track is true ·</span>}
        <span className="swatch ghost dashed" /> Ghost line
        <span className="legend-gap" />
        Your line, by speed vs ghost:
        {SPEED_BANDS.map((b) => (
          <span key={b.cls} className="legend-band"><i className={`band-${b.cls}`} />{b.label}</span>
        ))}
      </div>
    </div>
  );
}
