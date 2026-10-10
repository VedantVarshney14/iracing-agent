"""Line crossings and counted laps."""

from iagent.laps.segment import LapSegmenter
from iagent.live.events import define_event, define_state
from iagent.live.pipeline import Component

define_event("crossing", "The car crossed the start/finish line (every lap, counted or not). Components "
             "add what they know about the lap just finished; a counted one becomes a `lap`.",
             {"complete": "the lap ran line to line", "clean": "started at the line, no pit road"},
             internal=("segment",))
define_event("lap", "A counted lap, at the line: complete, from the line, no pit road.", {
    "lap": "laps counted so far, this one included", "lap_time": "s", "gap_s": "vs the reference (+ slower)",
    "complete": "always true", "clean": "always true",
}, log=True, judged=True, internal=("segment", "results"))
define_state(
    lap="laps counted so far (complete, from the line, not on pit road)",
    laps_driven="line crossings so far",
    line_at="session time of the last line crossing",
    after_line="just over the line with CrewChief running (it reads the lap time then)",
    learning="true during the learning laps (every corner cued)",
    ref_lap_time="the reference lap's time (s)",
)


class Laps(Component):
    def start(self):
        self._segmenter = LapSegmenter(self.ctx.session)
        self.state.update(lap=0, laps_driven=0, line_at=-1e9, after_line=False,
                          learning=self.settings.learning_laps > 0, ref_lap_time=self.ctx.plan.ref_lap_time,
                          track=self.ctx.session.track_key, car=self.ctx.session.car_key)

    def enrich_frame(self, e):
        segment = self._segmenter.push(e["frame"])
        if segment is not None:
            # Handled now, before the rest of this frame: the frame belongs to the new lap.
            self.pipe.dispatch("crossing", segment=segment, complete=segment.complete)
        s = self.settings
        self.state["after_line"] = s.crewchief and self.pipe.now - self.state["line_at"] < s.crewchief_quiet_s

    def on_crossing(self, e):
        self.state["laps_driven"] += 1
        self.state["line_at"] = e.at
        segment = e["segment"]
        if not (e["clean"] and segment.complete):
            return  # an out lap, or one joined midway: it doesn't count
        self.state["lap"] += 1
        self.state["learning"] = self.state["lap"] < self.settings.learning_laps
        ref = self.ctx.plan.ref_lap_time
        lap_time = segment.lap_time
        gap = round(lap_time - ref, 3) if lap_time and ref else None
        self.emit("lap", **{**e.fields, "lap": self.state["lap"], "lap_time": lap_time, "gap_s": gap})
