import { useMemo, useRef, useState, type PointerEvent } from "react";
import { extent, indexAt, polyline, tickStep, toLane, useWidth } from "../geometry";
import { metres, signed } from "../format";
import type { ChannelName, Corner, Review, Series } from "../types";

const LABEL_W = 140; // px, the readout column left of the plots

interface Lane {
  key: string;
  title: string;
  height: number;
  lap: Series;
  ref?: Series;
  domain: [number, number];
  zero?: boolean;
  format: (v: number) => string;
}

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
  const plotRef = useRef<HTMLDivElement>(null);
  const plotWidth = useWidth(plotRef);
  const [brush, setBrush] = useState<[number, number] | null>(null);

  const [r0, r1] = range;
  const lanes = useMemo<Lane[]>(() => {
    // Lanes whose scale depends on the data fit what's on screen, so a zoomed corner fills them.
    const [from, to] = [indexAt(dist, r0), indexAt(dist, r1) + 1];
    const visible = (s: Series) => s.slice(from, to);
    const abs = (s: Series) => visible(s).map((v) => (v == null ? null : Math.abs(v)));
    const channel = (name: ChannelName) => ({ lap: trace.lap[name] ?? [], ref: trace.ref[name] ?? [] });
    const [gLo, gHi] = extent(visible(trace.gap_s));
    const gPad = Math.max(0.05, (gHi - gLo) * 0.12);
    const speed = channel("Speed");
    const [sLo, sHi] = extent(visible(speed.lap), visible(speed.ref));
    const sPad = Math.max(3, (sHi - sLo) * 0.08);
    const gear = channel("Gear");
    const [, gearHi] = extent(gear.lap, gear.ref);
    const steer = channel("SteeringWheelAngle");
    const stHi = Math.max(5, extent(abs(steer.lap), abs(steer.ref))[1] * 1.1);
    const pct = (v: number) => `${Math.round(v)}%`;
    return [
      { key: "gap", title: "Gap to ghost s", height: 84, lap: trace.gap_s, domain: [gLo - gPad, gHi + gPad], zero: true, format: (v) => signed(v) },
      { key: "speed", title: "Speed km/h", height: 160, ...speed, domain: [sLo - sPad, sHi + sPad], format: (v) => v.toFixed(1) },
      { key: "throttle", title: "Throttle", height: 64, ...channel("Throttle"), domain: [-4, 104], format: pct },
      { key: "brake", title: "Brake", height: 64, ...channel("Brake"), domain: [-4, 104], format: pct },
      { key: "gear", title: "Gear", height: 56, ...gear, domain: [0, gearHi + 0.6], format: (v) => String(Math.round(v)) },
      { key: "steer", title: "Steering °", height: 72, ...steer, domain: [-stHi, stHi], zero: true, format: (v) => `${Math.round(v)}°` },
    ];
  }, [trace, dist, r0, r1]);

  const paths = useMemo(
    () =>
      lanes.map((lane) => ({
        lap: polyline(dist, toLane(lane.lap, ...lane.domain)),
        ref: lane.ref ? polyline(dist, toLane(lane.ref, ...lane.domain)) : "",
        zero: lane.zero ? toLane([0], ...lane.domain)[0] : null,
      })),
    [lanes, dist],
  );

  const span = r1 - r0;
  const viewBox = `${r0} 0 ${span} 100`;
  const pctOf = (d: number) => ((d - r0) / span) * 100;

  // Readouts follow the cursor, else sit at the selected corner's apex.
  const probe = cursor ?? corners.find((c) => c.id === primary)?.apex_m ?? null;
  const probeIdx = probe == null ? null : indexAt(dist, probe);

  const labels = useMemo(() => cornerLabels(corners, range, plotWidth), [corners, range, plotWidth]);
  const step = tickStep(span);
  const ticks: number[] = [];
  for (let d = Math.ceil(r0 / step) * step; d <= r1; d += step) ticks.push(d);

  const distanceAt = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return r0 + ((e.clientX - rect.left) / rect.width) * span;
  };
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const d = distanceAt(e);
    setBrush([d, d]);
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = distanceAt(e);
    onCursor(d);
    if (brush) setBrush([brush[0], d]);
  };
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!brush) return;
    const d = distanceAt(e);
    const [a, b] = [Math.min(brush[0], d), Math.max(brush[0], d)];
    setBrush(null);
    if (b - a > Math.max(15, span * 0.01)) {
      onZoom([a, b]);
    } else {
      const hit = corners.find((c) => d >= c.entry_m - 30 && d <= c.exit_m + 30);
      if (hit) onPickCorner(hit.id);
    }
  };

  const primaryCorner = corners.find((c) => c.id === primary);
  const zoomed = span < review.track.length_m - 1;

  return (
    <section className="card telemetry" aria-label="Telemetry">
      <div className="card-head">
        <h2>
          Telemetry <span className="muted">· drag to zoom, double-click to reset</span>
        </h2>
        <div className="row">
          <button type="button" className="btn" onClick={onAsk}>
            Ask coach
          </button>
          {primaryCorner && (
            <button
              type="button"
              className="btn"
              onClick={() => onZoom([primaryCorner.entry_m - 80, primaryCorner.exit_m + 80])}
            >
              Zoom to {primaryCorner.label}
            </button>
          )}
          {zoomed && (
            <button type="button" className="btn" onClick={() => onZoom(null)}>
              Whole lap
            </button>
          )}
        </div>
      </div>

      <div className="lanes-grid">
        <div className="lane-label probe">{probe == null ? "Hover the traces" : `@ ${metres(probe)}`}</div>
        <div className="corner-strip">
          {labels.map((c) => (
            <span key={c.id} style={{ left: `${pctOf(c.apex_m)}%` }} className={selected.includes(c.id) ? "on" : ""}>
              T{c.id}
            </span>
          ))}
        </div>
      </div>

      <div className="lanes">
        {lanes.map((lane, i) => (
          <div className="lanes-grid lane" key={lane.key}>
            <div className="lane-label">
              <span className="muted">{lane.title}</span>
              {probeIdx != null && (
                <span className="readout">
                  <Value v={lane.lap[probeIdx]} f={lane.format} cls={lane.ref ? "you" : ""} />
                  {lane.ref && <Value v={lane.ref[probeIdx]} f={lane.format} cls="ghost" />}
                </span>
              )}
            </div>
            <svg viewBox={viewBox} preserveAspectRatio="none" style={{ height: lane.height }} aria-hidden="true">
              {paths[i].zero != null && (
                <line x1={r0} x2={r1} y1={paths[i].zero!} y2={paths[i].zero!} className="zero" vectorEffect="non-scaling-stroke" />
              )}
              {paths[i].ref && <path d={paths[i].ref} className="trace ghost" vectorEffect="non-scaling-stroke" />}
              <path d={paths[i].lap} className={lane.ref ? "trace you" : "trace gap"} vectorEffect="non-scaling-stroke" />
            </svg>
          </div>
        ))}

        <div
          ref={plotRef}
          className="lanes-overlay"
          style={{ left: LABEL_W }}
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerLeave={() => onCursor(null)}
          onDoubleClick={() => onZoom(null)}
        >
          <svg viewBox={viewBox} preserveAspectRatio="none" aria-hidden="true">
            {corners
              .filter((c) => selected.includes(c.id))
              .map((c) => (
                <rect
                  key={c.id}
                  x={c.entry_m}
                  width={c.exit_m - c.entry_m}
                  y={0}
                  height={100}
                  className={c.id === primary ? "band primary" : "band"}
                />
              ))}
            {brush && (
              <rect x={Math.min(...brush)} width={Math.abs(brush[1] - brush[0])} y={0} height={100} className="brush" />
            )}
            {probe != null && (
              <line x1={probe} x2={probe} y1={0} y2={100} className="cursor" vectorEffect="non-scaling-stroke" />
            )}
          </svg>
        </div>
      </div>

      <div className="lanes-grid">
        <div className="lane-label muted">{zoomed ? `${metres(r0)} – ${metres(r1)}` : ""}</div>
        <div className="axis">
          {ticks.map((d) => (
            <span key={d} style={{ left: `${pctOf(d)}%` }}>
              {span > 2500 ? `${(d / 1000).toFixed(d % 1000 ? 1 : 0)} km` : `${Math.round(d)}`}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

function Value({ v, f, cls }: { v: number | null | undefined; f: (v: number) => string; cls: string }) {
  return <span className={cls}>{v == null ? "—" : f(v)}</span>;
}

/** Corner labels that fit: corners taken with a real speed drop first, then flat ones. */
function cornerLabels(corners: Corner[], [r0, r1]: [number, number], width: number): Corner[] {
  if (!width) return [];
  const pxPerM = width / (r1 - r0);
  const visible = corners.filter((c) => c.apex_m >= r0 && c.apex_m <= r1);
  const ordered = [...visible.filter((c) => !c.flat), ...visible.filter((c) => c.flat)];
  const kept: Corner[] = [];
  for (const c of ordered) {
    if (kept.every((k) => Math.abs(k.apex_m - c.apex_m) * pxPerM >= 34)) kept.push(c);
  }
  return kept;
}
