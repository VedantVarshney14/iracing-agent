"""The lap summary's words: lap time, gap, the corner that cost the most (or where a moment was).

Sets `summary_text` on `Lap`; saying it is a rule (`builtin_rules`). With CrewChief reading lap
times, only the coaching part.
"""

from iagent.live import phrasing
from iagent.live.events import Lap
from iagent.live.pipeline import Component, enrich


class Summary(Component):
    @enrich(Lap)
    def words(self, e: Lap):
        if e.lap_time is None:
            return
        s, cmap = self.settings, self.ctx.cmap
        worst_ok = (e.worst_corner is not None and e.worst_delta_s >= s.loss_s
                    and e.worst_corner not in self.state.focus_corners)
        moment = phrasing.corner_name(cmap.get(e.moment_at)) if e.moment_at is not None else None
        e.summary_text = phrasing.summary(e.lap_time, self.ctx.plan.ref_lap_time, e.worst_name if worst_ok else None,
                                          moment, s.crewchief)
