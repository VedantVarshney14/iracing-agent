"""The live coach's shared state: what components know and every rule can read.

One typed object, maintained by the components (each field says which) and read by the others and
by rules (as variables of the same names). Components talk only through events and this state.
"""

from dataclasses import dataclass, fields
from typing import Any

from iagent.live.events import doc


@dataclass(kw_only=True)
class CoachState:
    # the clock and the car (Clock, Track)
    time: float = doc("session time (s)", 0.0)
    since_start_s: float = doc("seconds since the coach started", 0.0)
    lap_dist: float | None = doc("distance from the line (m)")
    speed_kph: float | None = doc("current speed (km/h)")
    track: str = doc("track key", "")
    car: str = doc("car key", "")
    on_track: bool = doc("on the racing surface (or just off it), not on pit road", False)
    alongside: bool = doc("a car alongside (CrewChief's spotter is talking)", False)
    lap_clean: bool = doc("this lap started at the line and stayed off pit road", False)
    # laps (Laps)
    lap: int = doc("laps counted so far (complete, from the line, not on pit road)", 0)
    laps_driven: int = doc("line crossings so far", 0)
    line_at: float = doc("session time of the last line crossing", -1e9)
    after_line: bool = doc("just over the line with CrewChief running (it reads the lap time then)", False)
    learning: bool = doc("during the learning laps (every corner cued)", True)
    ref_lap_time: float | None = doc("the reference lap's time (s)")
    # pace (Pace)
    mode: str = doc('"pushing" or "tranquille"', "pushing")
    pushing: bool = doc("the driver is pushing", True)
    slow_for_s: float = doc("seconds into the current slow stretch (0 while pushing)", 0.0)
    settled: bool = doc("the slow stretch has lasted `debrief_after_s`: a cool-down, not a moment", False)
    stretch: int = doc("counts slow stretches", 0)
    best_lap: float | None = doc("the driver's best lap time here (s), or none")
    lap_times: list = doc("the pushing laps' times, in order", factory=list)
    # corners (History)
    struggling: set = doc("corners that went badly last time at pace", factory=set)
    big_trouble: set = doc("corners worth a word even with a focus set", factory=set)
    hints: dict = doc("corner -> hint for its cue next lap", factory=dict)
    # cues (Positions, CueCaller)
    next_cue_s: float = doc("seconds until the next timed point is due (inf: none)", float("inf"))
    in_corner: bool = doc("between a cue's point and the end of its corners", False)
    cue_heard: dict = doc("cue corner -> times heard in full", factory=dict)
    focus_heard: bool = doc("the current focus has been cued since it was set", True)
    # the focus (Focus)
    focus: int | None = doc("the focus cue's first corner, or none")
    focus_corners: list = doc("the focus cue's corners", factory=list)
    focus_label: str | None = doc('the focus as said ("Turns 15 and 16"), or none')
    focus_log: list = doc("every focus so far: {cue, corners, label, set_lap, done_lap, manual}", factory=list)
    # the debrief (Debrief)
    debriefs: int = doc("debriefs given this session", 0)

    @classmethod
    def docs(cls) -> dict[str, str]:
        return {f.name: f.metadata.get("doc", "") for f in fields(cls)}

    def variables(self) -> dict[str, Any]:
        return dict(self.__dict__)
