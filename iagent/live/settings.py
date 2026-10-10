"""The live coach's settings: each component reads the ones it needs."""

from dataclasses import dataclass


@dataclass
class Settings:
    learning_laps: int = 2  # laps with every corner cued
    lead_s: float = 0.8  # finish a cue this long before its target
    feedback_after_m: float = 60.0  # assess a corner this far past its exit
    loss_s: float = 0.08  # a corner that cost at least this much gets feedback / stays cued
    brake_m: float = 15.0  # brake point differences smaller than this aren't mentioned
    min_speed_kph: float = 5.0
    throttle_m: float = 25.0
    off_track_m: float = 5.0
    feedback_per_lap: int = 3
    feedback_expires_s: float = 8.0
    incident_s: float = 1.5  # a corner that cost more than this was an incident, not technique
    summary: bool = True
    focus: bool = True  # after the learning laps, coach one cue at a time
    focus_min_s: float = 0.15  # a cue must lose at least this (mean of the last two laps) to be the focus
    others_s: float = 0.25  # with a focus, other corners speak up only past this loss (or an off)
    pace_window_m: float = 400.0  # pace is judged over this much track
    push_ratio: float = 1.05  # back to pushing at this pace or better (vs own best)
    tranquille_ratio: float = 1.10  # tranquille at this pace or slower
    new_track_ratio: float = 1.25  # with no lap of their own yet, vs the reference
    short_cues: bool = True  # after a cue has been heard in full `learning_laps` times, say its short form
    debrief: bool = True  # talk through the last laps on a cool-down lap
    debrief_after_s: float = 15.0  # not pushing this long (not just a moment) before the debrief
    debrief_topics: int = 2
    narrate_after_s: float = 5.0  # ask the narrator this far into a slow stretch (it takes a few seconds)
    narrate_wait_s: float = 12.0  # once settled, wait this much longer for its words before our own
    crewchief: bool = False  # CrewChief is running: leave lap times to it, keep quiet after the line
    crewchief_quiet_s: float = 5.0
