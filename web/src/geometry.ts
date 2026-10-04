import { useEffect, useState, type RefObject } from "react";
import type { Series } from "./types";

/** Index of the grid point nearest distance `d` on an evenly spaced grid. */
export function indexAt(distance: number[], d: number): number {
  const step = distance.length > 1 ? distance[1] - distance[0] : 1;
  return Math.min(distance.length - 1, Math.max(0, Math.round((d - distance[0]) / step)));
}

/** SVG path through (x[i], y[i]) for i in [from, to), breaking the line at missing values. */
export function polyline(x: ArrayLike<number | null>, y: ArrayLike<number | null>, from = 0, to = x.length): string {
  let out = "";
  let pen = false;
  for (let i = from; i < to; i++) {
    const a = x[i];
    const b = y[i];
    if (a == null || b == null) {
      pen = false;
      continue;
    }
    out += `${pen ? "L" : "M"}${a.toFixed(1)},${b.toFixed(2)}`;
    pen = true;
  }
  return out;
}

/** Values scaled into 0 (top) ... 100 (bottom) for a lane with domain [lo, hi]. */
export function toLane(values: Series, lo: number, hi: number): Series {
  const span = hi - lo || 1;
  return values.map((v) => (v == null ? null : 100 - ((Math.min(hi, Math.max(lo, v)) - lo) / span) * 100));
}

export function extent(...series: (Series | undefined)[]): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (const s of series) {
    for (const v of s ?? []) {
      if (v == null) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  return Number.isFinite(lo) ? [lo, hi] : [0, 1];
}

/** A round tick spacing giving roughly `count` ticks over `span`. */
export function tickStep(span: number, count = 6): number {
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= raw) return m * mag;
  return 10 * mag;
}

/** The rendered width of an element, kept up to date. */
export function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
