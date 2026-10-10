"""The lap summary's words: lap time, gap, the corner that cost the most (or where a moment was).

Adds `summary_text` to `lap`; saying it is a rule (`builtin_rules`). With CrewChief reading lap
times, only the coaching part.
"""

from iagent.live import phrasing
from iagent.live.events import define_fields
from iagent.live.pipeline import Component

define_fields("lap", {"summary_text": "the lap summary as said, or none"})


class Summary(Component):
    def enrich_lap(self, e):
        s, st = self.settings, self.state
        if e["lap_time"] is None:
            e["summary_text"] = None
            return
        moment = e.get("moment_at")
        worst = e.get("worst_corner")
        worst_ok = (worst is not None and e["worst_delta_s"] >= s.loss_s and worst not in st["focus_corners"])
        e["summary_text"] = phrasing.summary(
            e["lap_time"], self.ctx.plan.ref_lap_time, e["worst_name"] if worst_ok else None,
            phrasing.corner_name(self.ctx.cmap.get(moment)) if moment is not None else None, s.crewchief)
