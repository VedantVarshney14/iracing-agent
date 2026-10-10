"""Watched points on track: an `approach` event in time to say something before the car gets there.

Anyone can ask (`watch`): the corner cues, a rule's `at` trigger. A watch's `speak` function says
how long what will be said takes (None: nothing to say right now, so it isn't timed); the event
comes when the car, at its current speed, would arrive `lead_s` after that line ends. A point
fires once per pass and re-arms once the car is well past it, so jitter can't fire it twice.

`next_cue_s` is when the next timed point is due, so less urgent speech can wait for room.
"""

import numpy as np

from iagent.live.events import define_event, define_state
from iagent.live.pipeline import Component

REARM_PAST_M = 100.0
HORIZON_S = 30.0  # points further away than this aren't considered yet

define_event("watch", "Ask for an `approach` event before the car reaches a point.", {
    "id": "the watch's name", "target_m": "the point (m from the line)", "lead_s": "finish this long before it",
    "tags": "fields copied into its approach events",
}, internal=("speak",))
define_event("unwatch", "Stop watching a point.", {"id": "the watch's name"})
define_event("approach", "The car is about to reach a watched point: a line started now finishes `lead_s` before it.", {
    "watch": "the watch's name", "source": "who asked (cue, rule)", "target_m": "the point (m)",
    "to_target_m": "how far it is (m)", "eta_s": "seconds to it at the current speed",
    "expires_at": "session time after which a line can no longer finish before it",
    "corner": ("corner", "the corner, if the point is one"), "name": "its name", "lead_s": "finish this long before it",
}, judged=True)
define_state(next_cue_s="seconds until the next timed point is due (inf: none)")


class Positions(Component):
    def start(self):
        self._watches: dict[str, dict] = {}
        self.state["next_cue_s"] = float("inf")

    def on_watch(self, e):
        self._watches[e["id"]] = {"id": e["id"], "target_m": e["target_m"], "lead_s": e["lead_s"],
                                  "speak": e["speak"], "tags": e.get("tags") or {}, "armed": True}

    def on_unwatch(self, e):
        self._watches.pop(e["id"], None)

    def on_frame(self, e):
        due = float("inf")
        if not self.state["on_track"]:
            self.state["next_cue_s"] = due
            return
        length = self.ctx.plan.length_m
        d, v = self.state["lap_dist"], max(e["frame"].get("Speed") or 0.0, 5.0)
        for w in self._watches.values():
            target = w["target_m"] % length
            to_target = (target - d) % length
            if to_target > length / 2:
                if (d - target) % length >= REARM_PAST_M:
                    w["armed"] = True  # clearly past: ready for the next pass
                continue
            speak = w["speak"]()
            if speak is None:
                continue  # nothing to say there now
            eta = to_target / v
            start_in = eta - speak - w["lead_s"]
            if start_in > 0:
                # For planning other speech, time the run as the reference lap did: the current speed
                # underestimates it badly when accelerating out of a slow corner.
                due = min(due, start_in, self._ref_time(d, target) - speak - w["lead_s"])
                continue
            if not w["armed"] or eta > HORIZON_S:
                continue
            w["armed"] = False
            self.emit("approach", **w["tags"], watch=w["id"], target_m=round(target, 1),
                      to_target_m=round(to_target, 1), eta_s=round(eta, 2), lead_s=w["lead_s"],
                      expires_at=e.at + max(0.0, eta - speak))
        self.state["next_cue_s"] = due

    def _ref_time(self, a: float, b: float) -> float:
        ref_d, ref_t = self.ctx.ref_d, self.ctx.ref_t
        lap = self.ctx.plan.ref_lap_time or float(ref_t[-1])
        return float((np.interp(b, ref_d, ref_t) - np.interp(a, ref_d, ref_t)) % lap)
