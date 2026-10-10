"""Corner exits: shortly after each corner, its metrics against the reference lap.

The metrics are the lap review's own (`corner_metrics`), computed on this lap's samples so far,
and the clearest cause of any lost time comes from `phrasing.CAUSES`. Corners driven while not
pushing are still reported (`at_pace` false), so rules can choose; only corners at pace count
towards the lap's results.
"""

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from iagent.analysis.corners import corner_metrics
from iagent.live import phrasing
from iagent.live.events import define_event, define_fields
from iagent.live.pipeline import Component

ROW_CHANNELS = ("LapDist", "Speed", "Brake", "Throttle", "Gear", "PlayerTrackSurface", "SessionTime")

METRICS = {
    "brake_m": "where braking began (m), none if not braked",
    "brake_peak": "peak brake pressure on the approach (0-1)",
    "min_throttle": "lowest throttle on the approach (0-1): a lift if well below 1",
    "entry_speed_kph": "fastest point before the apex",
    "min_speed_kph": "slowest point in the corner",
    "min_speed_m": "where the slowest point was (m)",
    "full_throttle_m": "back on full throttle (m), none if never in the segment",
    "exit_speed_kph": "speed at the corner's exit",
    "min_gear": "lowest gear",
    "off_track_m": "metres off track in the corner's segment",
    "time_s": "time through the corner's segment",
}
DIFFS = {  # name: (mine, reference, description)
    "brake_diff_m": ("brake_m", "brake_m", "brake_m - ref_brake_m: > 0 braked later"),
    "min_speed_diff_kph": ("min_speed_kph", "min_speed_kph", "min_speed_kph - ref_min_speed_kph: > 0 carried more speed"),
    "throttle_diff_m": ("full_throttle_m", "full_throttle_m", "full_throttle_m - ref_full_throttle_m: > 0 on full throttle later"),
    "exit_speed_diff_kph": ("exit_speed_kph", "exit_speed_kph", "exit_speed_kph - ref_exit_speed_kph"),
}

define_event("corner_exit", "Just past a corner's exit (`feedback_after_m`), with its metrics against the reference.", {
    "corner": ("corner", "the corner"), "name": "its name, or 'Turn N'",
    "delta_s": "time lost (+) or gained (-) vs the reference",
    "at_pace": "driven pushing on a counted lap", "pushing": "same as at_pace (what pushing_only looks at)",
    "cause": "the clearest cause of the loss (see phrasing.CAUSES), or none", "amount": "how much, for the cause",
    "advice": "what to say about it, or none", "hint": "for the corner's cue next lap, or none",
    "longer": "the advice with the why and the how", "struggling": "it went badly (lost time, or off)",
    **METRICS, **{f"ref_{k}": f"the reference lap's {k}" for k in METRICS},
    **{k: v[2] for k, v in DIFFS.items()},
}, judged=True, internal=("result",))
define_fields("crossing", {"corners": "[{corner, delta_s}] for the corners driven at pace"}, internal=("results",))
define_fields("lap", {"corners": "[{corner, delta_s}] for the corners driven at pace",
                      "worst_corner": "corner that lost the most", "worst_name": "its name",
                      "worst_delta_s": "how much it lost"})


@dataclass
class CornerResult:
    corner: int
    delta_s: float
    advice: str | None  # said after the corner
    hint: str | None  # added to the corner's cue next lap
    struggling: bool
    metrics: dict | None = None
    cause: str | None = None
    amount: float | None = None
    longer: str | None = None


class Corners(Component):
    def start(self):
        self._ref_metrics = {m["corner"]: m for m in self.ctx.plan.ref_metrics}
        self._new_lap()

    def _new_lap(self):
        self._rows: list[tuple[float, ...]] = []
        self._assessed: set[int] = set()
        self._results: list[CornerResult] = []

    def on_frame(self, e):
        if not self.state["on_track"]:
            return
        f = e["frame"]
        self._rows.append(tuple(float(f.get(c, np.nan)) for c in ROW_CHANNELS))
        d = self.state["lap_dist"]
        at_pace = self.state["lap_clean"] and self.state["pushing"]  # only pushing teaches anything
        for c in self.ctx.cmap.corners:
            at = min(c.exit_m + self.settings.feedback_after_m, c.segment_end_m - 1.0)
            if c.id in self._assessed or not at <= d <= c.segment_end_m + 1.0:
                continue
            self._assessed.add(c.id)
            result = self.assess(c.id, d)
            if result is None:
                continue
            if at_pace:
                self._results.append(result)
            self.emit("corner_exit", **self._fields(c, result, at_pace))

    def _fields(self, c, r: CornerResult, at_pace: bool) -> dict:
        mine, ref = r.metrics or {}, self._ref_metrics.get(c.id) or {}
        out = {"corner": c.id, "name": phrasing.corner_name(c), "delta_s": r.delta_s, "at_pace": at_pace,
               "pushing": at_pace, "cause": r.cause, "amount": r.amount, "advice": r.advice, "hint": r.hint,
               "longer": r.longer, "struggling": r.struggling, "result": r,
               **{k: mine.get(k) for k in METRICS}, **{f"ref_{k}": ref.get(k) for k in METRICS}}
        for key, (a, b, _) in DIFFS.items():
            out[key] = None if mine.get(a) is None or ref.get(b) is None else round(mine[a] - ref[b], 1)
        return out

    def assess(self, corner_id: int, d: float) -> CornerResult | None:
        c = self.ctx.cmap.get(corner_id)
        ref = self._ref_metrics.get(corner_id)
        lo = max(0.0, c.segment_start_m - 60.0)
        grid = self._grid(lo, d)
        if grid is None or ref is None or ref.get("missing"):
            return None
        mine = corner_metrics(grid, None, replace(self.ctx.cmap, corners=[c]))[0]
        if mine.get("missing"):
            return None
        t = grid["lap_time_s"].to_numpy()
        dist = grid["LapDist"].to_numpy()
        start = max(c.segment_start_m, float(dist[0]))
        ref_d, ref_t = self.ctx.ref_d, self.ctx.ref_t
        delta = float((np.interp(d, dist, t) - np.interp(start, dist, t))
                      - (np.interp(d, ref_d, ref_t) - np.interp(start, ref_d, ref_t)))
        s = self.settings
        struggling = delta >= s.loss_s or (mine.get("off_track_m") or 0) >= s.off_track_m
        cause, amount = phrasing.cause_of(mine, ref, delta, s)
        if delta > s.incident_s:
            cause = amount = None  # a spin or a moment: the numbers describe the incident, not the technique
        texts = phrasing.advice(phrasing.corner_name(c), cause, amount, ref, mine)
        return CornerResult(corner_id, round(delta, 3), texts["advice"], texts["hint"], struggling, mine,
                            cause.name if cause else None, amount, texts["longer"])

    def _grid(self, lo: float, hi: float) -> pd.DataFrame | None:
        """This lap from `lo` to `hi` on a 1 m grid, or None if it wasn't all driven."""
        if not self._rows:
            return None
        raw = np.array(self._rows, dtype=float)
        dist = np.maximum.accumulate(raw[:, 0])
        if dist[0] > lo + 5 or dist[-1] < hi - 5:
            return None
        grid = np.arange(lo, hi, 1.0)
        out = {"LapDist": grid}
        for i, name in enumerate(ROW_CHANNELS[1:], start=1):
            out["lap_time_s" if name == "SessionTime" else name] = np.interp(grid, dist, raw[:, i])
        out["Gear"] = np.round(out["Gear"])
        out["PlayerTrackSurface"] = np.round(out["PlayerTrackSurface"])
        return pd.DataFrame(out)

    def enrich_crossing(self, e):
        e["results"] = self._results
        e["corners"] = [{"corner": r.corner, "delta_s": r.delta_s} for r in self._results]
        self._new_lap()

    def enrich_lap(self, e):
        s = self.settings
        worst = max((r for r in e["results"] if r.delta_s <= s.incident_s), key=lambda r: r.delta_s, default=None)
        c = self.ctx.cmap.get(worst.corner) if worst else None
        e.fields.update(worst_corner=worst.corner if worst else None, worst_delta_s=worst.delta_s if worst else None,
                        worst_name=phrasing.corner_name(c) if c else None)
