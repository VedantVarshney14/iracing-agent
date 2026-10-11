"""Line crossings and counted laps."""

from dataclasses import fields

from iagent.laps.segment import LapSegmenter
from iagent.live.events import AtTheLine, Crossing, Frame, Lap
from iagent.live.pipeline import EventHandler, enrich, on


class Laps(EventHandler):
    """Each line crossing is a `Crossing` (handled before the rest of the frame: the frame belongs
    to the new lap); a complete, clean one is followed by a counted `Lap`."""

    def start(self):
        """Set up the lap segmenter and what the state knows from the start (learning laps,
        reference time, track and car)."""
        self._segmenter = LapSegmenter(self.ctx.session)
        s, st = self.settings, self.state
        st.learning = s.learning_laps > 0
        st.ref_lap_time = self.ctx.plan.ref_lap_time
        st.track, st.car = self.ctx.session.track_key, self.ctx.session.car_key

    @enrich(Frame)
    def segment(self, e: Frame):
        """Feed the frame to the segmenter; a line crossing is dispatched at once, so this frame
        counts in the new lap. Also keeps `after_line` (CrewChief's quiet time after the line).
        """
        segment = self._segmenter.push(e.frame)
        if segment is not None:
            self.pipe.dispatch(Crossing(segment=segment, complete=segment.complete))
        s = self.settings
        self.state.after_line = (
            s.crewchief and self.pipe.now - self.state.line_at < s.crewchief_quiet_s
        )

    @on(Crossing)
    def crossed(self, e: Crossing):
        """Count the crossing; a complete, clean lap is also counted as a `Lap`, carrying what
        handlers added at the line."""
        st = self.state
        st.laps_driven += 1
        st.line_at = e.at
        if not (e.clean and e.segment.complete):
            return  # an out lap, or one joined midway: it doesn't count
        st.lap += 1
        st.learning = st.lap < self.settings.learning_laps
        ref, lap_time = self.ctx.plan.ref_lap_time, e.segment.lap_time
        gap = round(lap_time - ref, 3) if lap_time and ref else None
        carried = {
            f.name: getattr(e, f.name) for f in fields(AtTheLine) if f.name != "at"
        }
        self.emit(Lap(**carried, lap=st.lap, lap_time=lap_time, gap_s=gap))
