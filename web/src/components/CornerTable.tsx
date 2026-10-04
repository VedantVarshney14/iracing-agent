import type { MouseEvent } from "react";
import { deltaColor, signed } from "../format";
import type { Corner } from "../types";

interface Props {
  corners: Corner[];
  totalDelta: number | null;
  selected: number[];
  primary: number | null;
  onPick: (id: number, add: boolean) => void;
}

export function CornerTable({ corners, totalDelta, selected, primary, onPick }: Props) {
  const worst = Math.max(0.1, ...corners.map((c) => Math.abs(c.delta_s ?? 0)));
  return (
    <section className="card corners" aria-label="Corners">
      <div className="card-head">
        <h2>
          Corners <span className="muted">· vs ghost</span>
        </h2>
        <span className="muted small">⌘/Ctrl-click to compare several · sum {signed(totalDelta)} s</span>
      </div>
      <div className="table-scroll">
        <div className="corner-grid head">
          <span>Corner</span>
          <span>Time</span>
          <span className="num" title="Positive: braked later than the ghost">Brake m</span>
          <span className="num" title="Positive: carried more speed">Min km/h</span>
          <span className="num" title="Positive: back on full throttle later">Full thr m</span>
          <span className="num" title="Positive: faster exit">Exit km/h</span>
        </div>
        {corners.map((c) => {
          const on = selected.includes(c.id);
          const off = (c.off_track_m ?? 0) > 0 || (c.ref_off_track_m ?? 0) > 0;
          return (
            <button
              key={c.id}
              type="button"
              aria-pressed={on}
              className={`corner-grid row-btn${on ? " on" : ""}${c.id === primary ? " primary" : ""}`}
              onClick={(e: MouseEvent) => onPick(c.id, e.metaKey || e.ctrlKey)}
            >
              <span className="ellipsis">
                <b>T{c.id}</b> <span className="muted">{c.name ?? ""}</span>
                {off && <span className="flag" title={`Off track: you ${c.off_track_m ?? 0} m, ghost ${c.ref_off_track_m ?? 0} m`}>off</span>}
              </span>
              <span className="delta-cell">
                <span className="mono" style={{ color: deltaColor(c.delta_s) }}>{signed(c.delta_s)}</span>
                <i style={{ width: `${(Math.abs(c.delta_s ?? 0) / worst) * 36 + 2}px`, background: deltaColor(c.delta_s) }} />
              </span>
              <span className="num mono">{signed(c.brake_diff_m, 0)}</span>
              <span className="num mono">{signed(c.min_speed_diff_kph, 1)}</span>
              <span className="num mono">{signed(c.full_throttle_diff_m, 0)}</span>
              <span className="num mono">{signed(c.exit_speed_diff_kph, 1)}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
