"""Corner exits: shortly after each corner, its metrics against the reference lap (`CornerExit`).

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
from iagent.live.events import CornerExit, Crossing, Frame, Lap
from iagent.live.pipeline import Component, enrich, on

ROW_CHANNELS = ("LapDist", "Speed", "Brake", "Throttle", "Gear", "PlayerTrackSurface", "SessionTime")


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

    @on(Frame)
    def watch(self, e: Frame):
        st = self.state
        if not st.on_track:
            return
        self._rows.append(tuple(float(e.frame.get(c, np.nan)) for c in ROW_CHANNELS))
        d = st.lap_dist
        at_pace = st.lap_clean and st.pushing  # only pushing teaches anything
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
            self.emit(CornerExit(
                corner=c.id, corner_name=phrasing.corner_name(c), delta_s=result.delta_s, at_pace=at_pace,
                pushing=at_pace, cause=result.cause, amount=result.amount, advice=result.advice, hint=result.hint,
                longer=result.longer, struggling=result.struggling, metrics=result.metrics or {},
                ref=self._ref_metrics.get(c.id) or {}, result=result))

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

    @enrich(Crossing)
    def lap_corners(self, e: Crossing):
        e.results = self._results
        e.corners = [{"corner": r.corner, "delta_s": r.delta_s} for r in self._results]
        self._new_lap()

    @enrich(Lap)
    def worst(self, e: Lap):
        s = self.settings
        worst = max((r for r in e.results if r.delta_s <= s.incident_s), key=lambda r: r.delta_s, default=None)
        if worst is not None:
            e.worst_corner, e.worst_delta_s = worst.corner, worst.delta_s
            e.worst_name = phrasing.corner_name(self.ctx.cmap.get(worst.corner))
