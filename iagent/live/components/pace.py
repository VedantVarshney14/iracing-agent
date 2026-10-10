"""Pushing or not: pace over the last stretch of track against the driver's own best lap.

Clearly slower (`tranquille_ratio`, 10%+) means tranquille: an out lap, a cool-down, or the few
hundred metres after a moment; back within `push_ratio` (5%) is pushing again. On a track the
driver hasn't lapped yet, the reference lap is the yardstick, generously (`new_track_ratio`).
A slow stretch that lasts is a cool-down: `slowing` is emitted `narrate_after_s` into it (time to
ask for the coach's words), `cool_down` at `debrief_after_s`.
"""

from collections import deque

import numpy as np

from iagent.live.events import define_event, define_fields, define_state
from iagent.live.pipeline import Component

define_event("pace", "The coach's judgement of the driver's pace changed.",
             {"mode": '"pushing" or "tranquille"', "lap_dist": "where (m)"}, log=True)
define_event("slowing", "A slow stretch has lasted `narrate_after_s` (not just a moment, probably).",
             {"stretch": "which slow stretch (counts up)"})
define_event("cool_down", "A slow stretch has lasted `debrief_after_s`: a cool-down, time to talk.",
             {"stretch": "which slow stretch"})
_LAP_PACE = {"pace": '"pushing", "moment" or "tranquille"', "pushing": "the lap counts (not tranquille)",
             "pushing_share": "share of the lap pushing (0-1)", "slow": "[from_m, to_m] stretches not pushing",
             "moment_at": "corner where a moment started, or none"}
define_fields("crossing", _LAP_PACE)
define_fields("lap", {**_LAP_PACE, "new_best": "a new best lap (pushing)",
                      "best_gap_s": "vs the best lap before this one (+ slower)"})
define_state(
    mode='"pushing" or "tranquille"',
    pushing="true while the driver is pushing",
    slow_for_s="seconds into the current slow stretch (0 while pushing)",
    settled="the slow stretch has lasted `debrief_after_s`: a cool-down, not a moment",
    stretch="counts slow stretches",
    best_lap="the driver's best lap time here (s), or none",
    lap_times="the pushing laps' times, in order",
)


class Pace(Component):
    def start(self):
        ctx = self.ctx
        self.length = ctx.plan.length_m
        own = ctx.own_best
        # The driver's own best lap (distance, elapsed time), or the reference until they have one.
        self._d, self._t, best = ctx.ref_d, ctx.ref_t, None
        self._own = own is not None
        if own is not None:
            self._d = own["LapDist"].to_numpy(dtype=float)
            self._t = own["lap_time_s"].to_numpy(dtype=float)
            best = float(self._t[-1])
        self._trail: deque[tuple[float, float]] = deque()  # (distance run, session time)
        self._run_m = 0.0
        self._prev_d: float | None = None
        self._frame_no = 0
        self._since: float | None = None
        self._sent: set[str] = set()
        self._new_lap()
        self.state.update(mode="pushing", pushing=True, slow_for_s=0.0, settled=False, stretch=0, best_lap=best,
                          lap_times=[])

    def _new_lap(self):
        self._frames = 0
        self._pushing_frames = 0
        self._slow: list[list[float]] = []  # [from_m, to_m] stretches not pushing, this lap

    def on_frame(self, e):
        if not self.state["on_track"]:
            return
        now, d = e.at, self.state["lap_dist"]
        if self._prev_d is not None:
            step = d - self._prev_d
            if step < -self.length / 2:
                step += self.length  # crossed the line
            if abs(step) > 200.0:  # a reset or tow: start judging afresh
                self._trail.clear()
                step = 0.0
            self._run_m += max(step, 0.0)
        self._prev_d = d
        self._trail.append((self._run_m, now))
        window = self.settings.pace_window_m
        while len(self._trail) > 2 and self._trail[1][0] <= self._run_m - window:
            self._trail.popleft()
        self._frames += 1
        self._frame_no += 1
        if self._frame_no % 6 == 0 and self._run_m - self._trail[0][0] >= window * 0.95:
            ratio = self.ratio(d)
            s = self.settings
            slow = s.tranquille_ratio if self._own else s.new_track_ratio
            back = s.push_ratio if self._own else s.new_track_ratio - 0.05
            if self.state["mode"] == "pushing" and ratio >= slow:
                self._set("tranquille", now, d)
            elif self.state["mode"] == "tranquille" and ratio <= back:
                self._set("pushing", now, d)
        if self.state["mode"] == "pushing":
            self._pushing_frames += 1
            return
        slow_for = now - self._since
        self.state["slow_for_s"] = slow_for
        self.state["settled"] = slow_for >= self.settings.debrief_after_s
        for event, after in (("slowing", self.settings.narrate_after_s), ("cool_down", self.settings.debrief_after_s)):
            if slow_for >= after and event not in self._sent:
                self._sent.add(event)
                self.emit(event, stretch=self.state["stretch"])

    def ratio(self, d: float) -> float:
        """Time over the last window against the pace lap over the same stretch (1.0: as fast)."""
        (m0, t0), (m1, t1) = self._trail[0], self._trail[-1]
        span = m1 - m0
        lap = self.state["best_lap"] or float(self._t[-1])
        a, b = np.interp((d - span) % self.length, self._d, self._t), np.interp(d, self._d, self._t)
        best = float((b - a) % lap) or 1e-6
        return (t1 - t0) / best

    def _set(self, mode: str, now: float, d: float) -> None:
        self.state.update(mode=mode, pushing=mode == "pushing", slow_for_s=0.0, settled=False)
        if mode == "tranquille":
            self.state["stretch"] += 1
            self._since, self._sent = now, set()
            self._slow.append([round(max(0.0, d - self.settings.pace_window_m / 2)), round(d)])
        elif self._slow:
            self._slow[-1][1] = round(d)
        self.emit("pace", mode=mode, lap_dist=round(d))

    def enrich_crossing(self, e):
        """How the lap just finished was driven: pushing (all the way), a moment (mostly pushing,
        one slow stretch: where it started), or tranquille (mostly not pushing)."""
        if self.state["mode"] == "tranquille" and self._slow:
            self._slow[-1][1] = round(self.length)  # still slow at the line
        share = self._pushing_frames / self._frames if self._frames else 0.0
        pace, moment_at = "pushing", None
        if self._slow:
            if share >= 0.6:  # mostly pushing: one moment (a spin and its recovery can run over a km)
                where = max(self._slow, key=lambda ab: ab[1] - ab[0])[0]
                moment_at = min(self.ctx.cmap.corners, key=lambda c: abs(c.apex_m - where)).id
                pace = "moment"
            else:
                pace = "tranquille"
        e.fields.update(pace=pace, pushing=pace != "tranquille", pushing_share=round(share, 2), slow=self._slow,
                        moment_at=moment_at)
        self._new_lap()

    def enrich_lap(self, e):
        """A faster pushing lap becomes the pace to judge pushing by."""
        best, lap_time = self.state["best_lap"], e["lap_time"]
        new = bool(e["pace"] == "pushing" and lap_time and (best is None or lap_time < best))
        e.fields.update(new_best=new, best_gap_s=round(lap_time - best, 3) if lap_time and best else None)
        if e["pace"] == "pushing" and lap_time:
            self.state["lap_times"] = [*self.state["lap_times"], lap_time]
        if new:
            seg = e["segment"].frames
            self._d = np.maximum.accumulate(seg["LapDist"].to_numpy(dtype=float))
            self._t = seg["lap_time_s"].to_numpy(dtype=float)
            self._own = True
            self.state["best_lap"] = lap_time

