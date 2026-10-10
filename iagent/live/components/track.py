"""Where the car is: on track or not, a car alongside, pit road."""

from iagent.live.events import Crossing, Frame, Pit
from iagent.live.pipeline import Component, enrich, on
from iagent.telemetry.frames import SURFACE_OFF_TRACK, SURFACE_ON_TRACK

CAR_ALONGSIDE = 2  # iRacing's CarLeftRight: 0 off, 1 clear, 2+ a car (or cars) alongside


class Track(Component):
    def start(self):
        self._pit: bool | None = None

    @on(Frame)
    def where(self, e: Frame):
        f, st = e.frame, self.state
        surface = f.get("PlayerTrackSurface", SURFACE_ON_TRACK)
        speed = f.get("Speed")
        st.time, st.lap_dist = e.at, f.get("LapDist")
        st.speed_kph = None if speed is None else speed * 3.6
        st.on_track = (surface in (SURFACE_ON_TRACK, SURFACE_OFF_TRACK) and not f.get("OnPitRoad", 0)
                       and st.lap_dist is not None and speed is not None)
        st.alongside = (f.get("CarLeftRight") or 0) >= CAR_ALONGSIDE
        if not st.on_track:
            st.lap_clean = False
        pit = bool(f.get("OnPitRoad", 0))
        if self._pit is not None and pit != self._pit:
            self.emit(Pit(pit="entry" if pit else "exit"))
        self._pit = pit

    @enrich(Crossing)
    def clean(self, e: Crossing):
        e.clean = self.state.lap_clean
        self.state.lap_clean = True
