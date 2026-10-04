import { useMemo, useRef, type PointerEvent, type ReactNode } from "react";
import { fillBetween, indexAt, polyline, tickStep, toLane, useWidth } from "../geometry";
import { metres } from "../format";
import type { Series } from "../types";

export interface LaneLine {
  values: Series;
  cls: "you" | "ghost" | "gap" | "offset";
}

export interface Lane {
  key: string;
  title: ReactNode;
  height: number;
  lines: LaneLine[];
  domain: [number, number];
  zero?: boolean;
  // Shade between lines[0] and lines[1] (or lines[0] and zero): blue where the first is higher.
  fill?: "between" | "zero";
  // Whether "higher" is good for the driver (blue) or bad (orange), e.g. line offset: neither.
  fillGood?: "above" | "below" | "neutral";
  format: (v: number) => string;
}

export interface Marker {
  d: number;
  who: "you" | "ghost";
  label: string;
  dashed?: boolean;
}

interface Props {
  dist: number[];
  range: [number, number];
  lanes: Lane[];
  markers: Marker[];
  band?: [number, number] | null;
  cursor: number | null;
  onCursor: (d: number | null) => void;
}

const LABEL_W = 150;

/** Stacked channel lanes on a shared distance axis, with a hover cursor and readouts. */
export function Lanes({ dist, range, lanes, markers, band, cursor, onCursor }: Props) {
  const plotRef = useRef<HTMLDivElement>(null);
  const width = useWidth(plotRef);
  const [r0, r1] = range;
  const span = r1 - r0;
  const [from, to] = [Math.max(0, indexAt(dist, r0) - 2), Math.min(dist.length, indexAt(dist, r1) + 3)];

  const drawn = useMemo(
    () =>
      lanes.map((lane) => {
        const [lo, hi] = lane.domain;
        const fill =
          lane.fill && lane.lines[0]
            ? fillBetween(dist, lane.lines[0].values, lane.fill === "between" ? lane.lines[1]?.values ?? null : null, lo, hi, from, to)
            : null;
        return {
          lines: lane.lines.map((l) => polyline(dist, toLane(l.values, lo, hi), from, to)),
          fill,
          zero: lane.zero ? toLane([0], lo, hi)[0] : null,
        };
      }),
    [lanes, dist, from, to],
  );

  const probeIdx = cursor == null ? null : indexAt(dist, cursor);
  const step = tickStep(span, Math.max(3, Math.round(width / 110)));
  const ticks: number[] = [];
  for (let d = Math.ceil(r0 / step) * step; d <= r1; d += step) ticks.push(d);
  const pct = (d: number) => ((d - r0) / span) * 100;
  const visibleMarkers = markers.filter((m) => m.d >= r0 && m.d <= r1);

  const distanceAt = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return r0 + ((e.clientX - rect.left) / rect.width) * span;
  };

  return (
    <div className="lanes-chart">
      <div className="lanes-grid" style={{ gridTemplateColumns: `${LABEL_W}px minmax(0, 1fr)` }}>
        <div className="lane-label probe">{cursor == null ? "Hover the traces" : `@ ${metres(cursor)}`}</div>
        <div className="marker-strip">
          {visibleMarkers.map((m, i) => (
            <span key={i} className={m.who} style={{ left: `${pct(m.d)}%`, top: (i % 2) * 14 }}>{m.label}</span>
          ))}
        </div>
      </div>
      <div className="lanes">
        {lanes.map((lane, i) => (
          <div className="lanes-grid lane" key={lane.key} style={{ gridTemplateColumns: `${LABEL_W}px minmax(0, 1fr)` }}>
            <div className="lane-label">
              <span className="muted">{lane.title}</span>
              {probeIdx != null && (
                <span className="readout">
                  {lane.lines.map((l, j) => {
                    const v = l.values[probeIdx];
                    return <span key={j} className={l.cls}>{v == null ? "—" : lane.format(v)}</span>;
                  })}
                </span>
              )}
            </div>
            <svg viewBox={`${r0} 0 ${span} 100`} preserveAspectRatio="none" style={{ height: lane.height }} aria-hidden="true">
              {drawn[i].fill && (
                <>
                  <path d={drawn[i].fill!.above} className={`fill ${goodness(lane, "above")}`} />
                  <path d={drawn[i].fill!.below} className={`fill ${goodness(lane, "below")}`} />
                </>
              )}
              {drawn[i].zero != null && (
                <line x1={r0} x2={r1} y1={drawn[i].zero!} y2={drawn[i].zero!} className="zero" vectorEffect="non-scaling-stroke" />
              )}
              {drawn[i].lines.map((d, j) => (
                <path key={j} d={d} className={`trace ${lane.lines[j].cls}`} vectorEffect="non-scaling-stroke" />
              ))}
            </svg>
          </div>
        ))}
        <div
          ref={plotRef}
          className="lanes-overlay"
          style={{ left: LABEL_W }}
          onPointerMove={(e) => onCursor(distanceAt(e))}
          onPointerLeave={() => onCursor(null)}
        >
          <svg viewBox={`${r0} 0 ${span} 100`} preserveAspectRatio="none" aria-hidden="true">
            {band && <rect x={band[0]} width={band[1] - band[0]} y={0} height={100} className="band soft" />}
            {visibleMarkers.map((m, i) => (
              <line key={i} x1={m.d} x2={m.d} y1={0} y2={100} className={`event ${m.who}${m.dashed ? " dashed" : ""}`} vectorEffect="non-scaling-stroke" />
            ))}
            {cursor != null && <line x1={cursor} x2={cursor} y1={0} y2={100} className="cursor" vectorEffect="non-scaling-stroke" />}
          </svg>
        </div>
      </div>
      <div className="lanes-grid" style={{ gridTemplateColumns: `${LABEL_W}px minmax(0, 1fr)` }}>
        <span />
        <div className="axis">
          {ticks.map((d) => (
            <span key={d} style={{ left: `${pct(d)}%` }}>{Math.round(d).toLocaleString("en-US")}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function goodness(lane: Lane, side: "above" | "below"): string {
  if (lane.fillGood === "neutral") return side === "above" ? "neutral-a" : "neutral-b";
  const good = lane.fillGood ?? "above";
  return side === good ? "gain" : "loss";
}
