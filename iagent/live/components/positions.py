"""Watched points on track: an `Approach` in time to say something before the car gets there.

Anyone can ask (`Watch`): the corner cues, a rule's `at` trigger. A watch's `speak` says how long
what will be said takes (None: nothing to say right now, so it isn't timed); the approach comes
when the car, at its current speed, would arrive `lead_s` after that line ends. A point fires once
per pass and re-arms once the car is well past it, so jitter can't fire it twice. `next_cue_s` in
the state is when the next timed point is due, so less urgent speech can wait for room.
"""

from dataclasses import dataclass

import numpy as np

from iagent.live.events import Approach, Frame, Unwatch, Watch
from iagent.live.pipeline import Component, on

REARM_PAST_M = 100.0
HORIZON_S = 30.0  # points further away than this aren't considered yet


@dataclass
class _Point:
    watch: Watch
    armed: bool = True


class Positions(Component):
    def start(self):
        self._points: dict[str, _Point] = {}

    @on(Watch)
    def add(self, e: Watch):
        self._points[e.id] = _Point(e)

    @on(Unwatch)
    def remove(self, e: Unwatch):
        self._points.pop(e.id, None)

    @on(Frame)
    def approach(self, e: Frame):
        st, due = self.state, float("inf")
        if not st.on_track:
            st.next_cue_s = due
            return
        length = self.ctx.plan.length_m
        d, v = st.lap_dist, max(e.frame.get("Speed") or 0.0, 5.0)
        for p in self._points.values():
            w = p.watch
            target = w.target_m % length
            to_target = (target - d) % length
            if to_target > length / 2:
                if (d - target) % length >= REARM_PAST_M:
                    p.armed = True  # clearly past: ready for the next pass
                continue
            speak = w.speak()
            if speak is None:
                continue  # nothing to say there now
            eta = to_target / v
            start_in = eta - speak - w.lead_s
            if start_in > 0:
                # For planning other speech, time the run as the reference lap did: the current speed
                # underestimates it badly when accelerating out of a slow corner.
                due = min(due, start_in, self._ref_time(d, target) - speak - w.lead_s)
                continue
            if not p.armed or eta > HORIZON_S:
                continue
            p.armed = False
            self.emit(Approach(**w.tags, watch=w.id, target_m=round(target, 1), to_target_m=round(to_target, 1),
                               eta_s=round(eta, 2), lead_s=w.lead_s, expires_at=e.at + max(0.0, eta - speak)))
        st.next_cue_s = due

    def _ref_time(self, a: float, b: float) -> float:
        ref_d, ref_t = self.ctx.ref_d, self.ctx.ref_t
        lap = self.ctx.plan.ref_lap_time or float(ref_t[-1])
        return float((np.interp(b, ref_d, ref_t) - np.interp(a, ref_d, ref_t)) % lap)
