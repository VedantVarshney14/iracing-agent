import { useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";
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
  // Whether "higher" is good for the driver (blue) or bad (orange), or neither.
  fillGood?: "above" | "below" | "neutral";
  // Bars instead of (or as well as) lines, e.g. time lost per 10 m: + is a loss (orange, up).
  bars?: { start: number; end: number; value: number }[];
  format: (v: number) => string;
  readout?: (i: number) => ReactNode; // overrides the per-line readout at the cursor
}

export interface Marker {
  d: number;
  who: "you" | "ghost";
  label: string;
  dashed?: boolean;
}

export interface Band {
  start: number;
  end: number;
  strong?: boolean;
}

export interface Label {
  d: number;
  text: string;
  on?: boolean;
  priority?: number; // higher is kept first when labels would overlap
}

interface Props {
  dist: number[];
  range: [number, number];
  lanes: Lane[];
  markers?: Marker[];
  bands?: Band[];
  labels?: Label[];
  cursor: number | null;
  probe?: number | null; // where readouts sit when the pointer is elsewhere
  onCursor: (d: number | null) => void;
  onZoom?: (range: [number, number] | null) => void; // enables drag-to-zoom and double-click reset
  onPick?: (d: number) => void; // a click (not a drag) at distance d
}

const LABEL_W = 150;
const COLUMNS = { gridTemplateColumns: `${LABEL_W}px minmax(0, 1fr)` };

/** Stacked channel lanes on a shared distance axis, with a hover cursor and readouts. */
export function Lanes({ dist, range, lanes, markers = [], bands = [], labels = [], cursor, probe, onCursor, onZoom, onPick }: Props) {
  const plotRef = useRef<HTMLDivElement>(null);
  const width = useWidth(plotRef);
  const [brush, setBrush] = useState<[number, number] | null>(null);
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

  const at = cursor ?? probe ?? null;
  const atIdx = at == null ? null : indexAt(dist, at);
  const step = tickStep(span, Math.max(3, Math.round(width / 110)));
  const ticks: number[] = [];
  for (let d = Math.ceil(r0 / step) * step; d <= r1; d += step) ticks.push(d);
  const pct = (d: number) => ((d - r0) / span) * 100;
  const visibleMarkers = markers.filter((m) => m.d >= r0 && m.d <= r1);
  const shownLabels = useMemo(() => fitLabels(labels, range, width), [labels, range, width]);

  const distanceAt = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return r0 + ((e.clientX - rect.left) / rect.width) * span;
  };
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (!onZoom && !onPick) return;
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
    if (onZoom && b - a > Math.max(15, span * 0.01)) onZoom([a, b]);
    else onPick?.(d);
  };

  return (
    <div className="lanes-chart">
      <div className="lanes-grid" style={COLUMNS}>
        <div className="lane-label probe">{at == null ? "Hover the traces" : `@ ${metres(at)}`}</div>
        <div className="strip-stack">
          {shownLabels.length > 0 && (
            <div className="corner-strip">
              {shownLabels.map((l) => (
                <span key={`${l.d}-${l.text}`} style={{ left: `${pct(l.d)}%` }} className={l.on ? "on" : ""}>{l.text}</span>
              ))}
            </div>
          )}
          {visibleMarkers.length > 0 && (
            <div className="marker-strip">
              {visibleMarkers.map((m, i) => (
                <span key={i} className={m.who} style={{ left: `${pct(m.d)}%`, top: (i % 2) * 14 }}>{m.label}</span>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="lanes">
        {lanes.map((lane, i) => (
          <div className="lanes-grid lane" key={lane.key} style={COLUMNS}>
            <div className="lane-label">
              <span className="muted">{lane.title}</span>
              {atIdx != null && (
                <span className="readout">
                  {lane.readout
                    ? lane.readout(atIdx)
                    : lane.lines.map((l, j) => {
                        const v = l.values[atIdx];
                        return <span key={j} className={l.cls}>{v == null ? "—" : lane.format(v)}</span>;
                      })}
                </span>
              )}
            </div>
            <svg viewBox={`${r0} 0 ${span} 100`} preserveAspectRatio="none" style={{ height: lane.height }} aria-hidden="true">
              {lane.bars?.map((b, k) => {
                if (b.end < r0 || b.start > r1) return null;
                const [lo, hi] = lane.domain;
                const y0 = toLane([0], lo, hi)[0]!;
                const y1 = toLane([b.value], lo, hi)[0]!;
                const gapW = (b.end - b.start) * 0.12;
                return (
                  <rect key={k} x={b.start + gapW / 2} width={b.end - b.start - gapW} y={Math.min(y0, y1)} height={Math.abs(y1 - y0)}
                    className={b.value > 0 ? "bar loss" : "bar gain"} />
                );
              })}
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
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerLeave={() => onCursor(null)}
          onDoubleClick={() => onZoom?.(null)}
        >
          <svg viewBox={`${r0} 0 ${span} 100`} preserveAspectRatio="none" aria-hidden="true">
            {bands.map((b, i) => (
              <rect key={i} x={b.start} width={b.end - b.start} y={0} height={100} className={b.strong ? "band primary" : "band soft"} />
            ))}
            {visibleMarkers.map((m, i) => (
              <line key={i} x1={m.d} x2={m.d} y1={0} y2={100} className={`event ${m.who}${m.dashed ? " dashed" : ""}`} vectorEffect="non-scaling-stroke" />
            ))}
            {brush && <rect x={Math.min(...brush)} width={Math.abs(brush[1] - brush[0])} y={0} height={100} className="brush" />}
            {at != null && <line x1={at} x2={at} y1={0} y2={100} className="cursor" vectorEffect="non-scaling-stroke" />}
          </svg>
        </div>
      </div>
      <div className="lanes-grid" style={COLUMNS}>
        <span className="muted small">{span < (dist[dist.length - 1] ?? 0) - 1 ? `${metres(r0)} – ${metres(r1)}` : ""}</span>
        <div className="axis">
          {ticks.map((d) => (
            <span key={d} style={{ left: `${pct(d)}%` }}>
              {span > 2500 ? `${(d / 1000).toFixed(d % 1000 ? 1 : 0)} km` : Math.round(d).toLocaleString("en-US")}
            </span>
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

/** Labels that fit at this width, most important first. */
function fitLabels(labels: Label[], [r0, r1]: [number, number], width: number): Label[] {
  if (!width) return [];
  const pxPerM = width / (r1 - r0);
  const kept: Label[] = [];
  for (const l of [...labels].filter((l) => l.d >= r0 && l.d <= r1).sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))) {
    if (kept.every((k) => Math.abs(k.d - l.d) * pxPerM >= 34)) kept.push(l);
  }
  return kept;
}
