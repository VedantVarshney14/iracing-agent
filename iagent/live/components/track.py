"""Where the car is: on track or not, a car alongside, pit road."""

from iagent.live.events import define_event, define_state
from iagent.live.pipeline import Component
from iagent.telemetry.frames import SURFACE_OFF_TRACK, SURFACE_ON_TRACK

CAR_ALONGSIDE = 2  # iRacing's CarLeftRight: 0 off, 1 clear, 2+ a car (or cars) alongside

define_event("pit", "Pit road entry or exit.", {"pit": '"entry" or "exit"'})
define_state(
    on_track="on the racing surface (or just off it), not on pit road",
    alongside="a car alongside (CrewChief's spotter is talking)",
    lap_clean="this lap started at the line and stayed off pit road",
)


class Track(Component):
    def start(self):
        self.state.update(on_track=False, alongside=False, lap_clean=False)  # joined mid-lap
        self._pit = None

    def on_frame(self, e):
        f = e["frame"]
        surface = f.get("PlayerTrackSurface", SURFACE_ON_TRACK)
        on_track = (surface in (SURFACE_ON_TRACK, SURFACE_OFF_TRACK) and not f.get("OnPitRoad", 0)
                    and f.get("LapDist") is not None and f.get("Speed") is not None)
        speed = f.get("Speed")
        self.state.update(on_track=on_track, alongside=(f.get("CarLeftRight") or 0) >= CAR_ALONGSIDE,
                          time=e.at, lap_dist=f.get("LapDist"), speed_kph=None if speed is None else speed * 3.6)
        if not on_track:
            self.state["lap_clean"] = False
        pit = bool(f.get("OnPitRoad", 0))
        if self._pit is not None and pit != self._pit:
            self.emit("pit", pit="entry" if pit else "exit")
        self._pit = pit

    def enrich_crossing(self, e):
        e["clean"] = self.state["lap_clean"]
        self.state["lap_clean"] = True
