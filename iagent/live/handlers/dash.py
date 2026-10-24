"""The wheel dashboard's feed: what the Conspit dashboard shows, in a JSON file the SimHub plugin
(`simhub/CoachPlugin`) reads.

For the coming corner: what it is (number, name, brake or lift, gear, direction, where to brake),
a few words on what to change from last lap, and the brake and throttle through it on the last lap
and on the reference lap. For the lap: each corner's share of it coloured by time gained or lost so
far, the next corner and the focus. The file is written whole, and only when something on it
changes (a few times a lap), so the plugin never reads half of it.
"""

import json
import os
from pathlib import Path

import numpy as np

from iagent.live.cues import BEFORE_TURN_IN_M, HARD_BRAKE, LIFT_THROTTLE
from iagent.live.events import CornerExit, Crossing, FocusChanged, Frame
from iagent.live.phrasing import CAUSE_BY_NAME
from iagent.live.pipeline import EventHandler, on

DEFAULT_PATH = Path.home() / ".iagent" / "dash.json"
POINTS = 96  # samples per pedal trace
BEFORE_M = (
    120.0  # a trace starts this far before the reference brake point (or turn-in) ...
)
AFTER_M = 60.0  # ... and ends this far past the exit, or full throttle if that's later


class Dash(EventHandler):
    """Keeps the dashboard's feed (`settings.dash_file`) up to date; does nothing without one."""

    def start(self):
        """Set up, and write the feed once so the dashboard has something from the start."""
        self._path = Path(self.settings.dash_file) if self.settings.dash_file else None
        if self._path is None:
            return
        self._ref = {m["corner"]: m for m in self.ctx.plan.ref_metrics}
        self._rows: list[tuple[float, float, float]] = (
            []
        )  # this lap: distance, brake, throttle
        self._last: tuple[np.ndarray, ...] | None = None  # the same for the last lap
        self._words: dict[int, str] = {}  # corner -> what to change, from last lap
        self._last_brake: dict[int, float | None] = (
            {}
        )  # corner -> where braking began last lap
        self._deltas: dict[int, float] = {}  # corner -> time lost (+) this lap
        self._next: int | None = None
        self._seq = 0
        self._written: dict | None = None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write()

    @on(Frame)
    def drive(self, e: Frame):
        """Keep this lap's pedals; once past a corner's exit, show the next one."""
        st = self.state
        if self._path is None or st.lap_dist is None:
            return
        if st.on_track:
            f = e.frame
            self._rows.append(
                (st.lap_dist, float(f.get("Brake", 0.0)), float(f.get("Throttle", 0.0)))
            )
        corners = self.ctx.cmap.corners
        coming = next((c for c in corners if c.exit_m > st.lap_dist), corners[0])
        if coming.id != self._next:
            self._next = coming.id
            self._write()

    @on(Crossing)
    def new_lap(self, e: Crossing):
        """This lap's pedals become the last lap's, and the lap's corners go back to not driven."""
        if self._path is None:
            return
        if self._rows:
            rows = np.array(self._rows, dtype=float)
            self._last = (np.maximum.accumulate(rows[:, 0]), rows[:, 1], rows[:, 2])
        self._rows, self._deltas = [], {}
        self._write()

    @on(CornerExit)
    def judged(self, e: CornerExit):
        """Note the corner's time and brake point, and, driven at pace, what to change next lap."""
        if self._path is None:
            return
        self._deltas[e.corner] = e.delta_s
        self._last_brake[e.corner] = e.metrics.get("brake_m")
        cause = CAUSE_BY_NAME.get(e.cause) if e.at_pace and e.cause else None
        self._words[e.corner] = cause.dash(e.amount, e.ref) if cause else ""
        self._write()

    @on(FocusChanged)
    def focus(self, e: FocusChanged):
        if self._path is not None:
            self._write()

    # --- the feed ------------------------------------------------------------------------------

    def _write(self):
        """Write the feed if anything on it changed, with `seq` counting the changes."""
        feed = self._feed()
        if feed == self._written:
            return
        self._written = feed
        self._seq += 1
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**feed, "seq": self._seq}))
        os.replace(tmp, self._path)

    def _feed(self) -> dict:
        cmap, st = self.ctx.cmap, self.state
        focus = st.focus_corners
        return {
            "length_m": cmap.length_m,
            "corner": self._corner() if self._next else None,
            "cue": self._words.get(self._next, "") if self._next else "",
            "focus": "FOCUS " + "/".join(str(c) for c in focus) if focus else "",
            "trace": self._trace() if self._next else None,
            "strip": self._strip(),
        }

    def _corner(self) -> dict:
        """The coming corner as the dashboard shows it, and where to brake (or lift) for it."""
        c, m = self.ctx.cmap.get(self._next), self._ref.get(self._next) or {}
        brake_m = m.get("brake_m")
        if m.get("missing"):
            action, target = "", c.entry_m - BEFORE_TURN_IN_M
        elif brake_m is not None:
            action, target = "BRAKE", brake_m
        else:
            lift = (m.get("min_throttle") or 1.0) < LIFT_THROTTLE
            action, target = "LIFT" if lift else "FLAT", c.entry_m - BEFORE_TURN_IN_M
        return {
            "id": c.id,
            "name": c.name or "",
            "direction": c.direction,
            "action": action,
            "hard": action == "BRAKE" and (m.get("brake_peak") or 0.0) >= HARD_BRAKE,
            "gear": m.get("min_gear"),
            "target_m": round(float(target) % self.ctx.cmap.length_m, 1),
        }

    def _trace(self) -> dict:
        """Brake and throttle (0-1) through the coming corner, last lap's and the reference's, at
        `POINTS` evenly spaced points; where the apex is and, when the brake points differ enough
        to mention, last lap's and the reference's (0-1 across the trace)."""
        c, m = self.ctx.cmap.get(self._next), self._ref.get(self._next) or {}
        ref_brake = m.get("brake_m")
        lo = max(0.0, (ref_brake if ref_brake is not None else c.entry_m) - BEFORE_M)
        hi = min(
            self.ctx.cmap.length_m,
            max(c.exit_m, m.get("full_throttle_m") or 0.0) + AFTER_M,
        )
        xs = np.linspace(lo, hi, POINTS)
        at = lambda d: round((d - lo) / (hi - lo), 4)  # noqa: E731
        grid = self.ctx.ref_grid
        ref_d = grid["LapDist"].to_numpy(dtype=float)
        out = {
            "target_brake": _pedal(xs, ref_d, grid["Brake"].to_numpy(dtype=float)),
            "target_throttle": _pedal(
                xs, ref_d, grid["Throttle"].to_numpy(dtype=float)
            ),
            "last_brake": None,
            "last_throttle": None,
            "apex": at(c.apex_m),
            "arrow": None,
        }
        if (
            self._last is not None
            and self._last[0][0] <= lo + 5
            and self._last[0][-1] >= hi - 5
        ):
            dist, brake, throttle = self._last
            out["last_brake"], out["last_throttle"] = (
                _pedal(xs, dist, brake),
                _pedal(xs, dist, throttle),
            )
            mine = self._last_brake.get(c.id)
            if mine is not None and ref_brake is not None:
                if abs(mine - ref_brake) >= self.settings.brake_m:
                    out["arrow"] = [at(mine), at(ref_brake)]
        return out

    def _strip(self) -> dict:
        """The lap as one strip: each corner's segment, as gained, lost a little, lost a lot or not
        driven yet this lap; the next corner's turn-in and the focus corners' segments (0-1).
        """
        cmap, loss = self.ctx.cmap, self.settings.loss_s
        x = lambda d: round(d / cmap.length_m, 4)  # noqa: E731

        def state(corner: int) -> str:
            delta = self._deltas.get(corner)
            if delta is None:
                return "todo"
            return "gain" if delta <= 0 else "small" if delta < loss else "loss"

        return {
            "sectors": [
                {
                    "from": x(c.segment_start_m),
                    "to": x(c.segment_end_m),
                    "state": state(c.id),
                }
                for c in cmap.corners
            ],
            "next": x(cmap.get(self._next).entry_m) if self._next else None,
            "focus": [
                [x(cmap.get(c).segment_start_m), x(cmap.get(c).segment_end_m)]
                for c in self.state.focus_corners
            ],
        }


def _pedal(xs: np.ndarray, dist: np.ndarray, values: np.ndarray) -> list[float]:
    """VALUES (by DIST) at XS, lightly smoothed so the drawn line has no kinks, 0-1."""
    v = np.interp(xs, dist, np.nan_to_num(values))
    v = np.convolve(np.pad(v, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    return [round(float(p), 3) for p in np.clip(v, 0.0, 1.0)]
