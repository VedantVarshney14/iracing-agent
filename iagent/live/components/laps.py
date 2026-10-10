"""Line crossings and counted laps."""

from dataclasses import fields

from iagent.laps.segment import LapSegmenter
from iagent.live.events import Crossing, Frame, Lap
from iagent.live.pipeline import Component, enrich, on


class Laps(Component):
    """Each line crossing is a `Crossing` (handled before the rest of the frame: the frame belongs
    to the new lap); a complete, clean one becomes a counted `Lap`."""

    def start(self):
        self._segmenter = LapSegmenter(self.ctx.session)
        s, st = self.settings, self.state
        st.learning = s.learning_laps > 0
        st.ref_lap_time = self.ctx.plan.ref_lap_time
        st.track, st.car = self.ctx.session.track_key, self.ctx.session.car_key

    @enrich(Frame)
    def segment(self, e: Frame):
        segment = self._segmenter.push(e.frame)
        if segment is not None:
            self.pipe.dispatch(Crossing(segment=segment, complete=segment.complete))
        s = self.settings
        self.state.after_line = s.crewchief and self.pipe.now - self.state.line_at < s.crewchief_quiet_s

    @on(Crossing)
    def crossed(self, e: Crossing):
        st = self.state
        st.laps_driven += 1
        st.line_at = e.at
        if not (e.clean and e.segment.complete):
            return  # an out lap, or one joined midway: it doesn't count
        st.lap += 1
        st.learning = st.lap < self.settings.learning_laps
        ref, lap_time = self.ctx.plan.ref_lap_time, e.segment.lap_time
        gap = round(lap_time - ref, 3) if lap_time and ref else None
        carried = {f.name: getattr(e, f.name) for f in fields(Crossing) if f.name != "at"}
        self.emit(Lap(**carried, lap=st.lap, lap_time=lap_time, gap_s=gap))
