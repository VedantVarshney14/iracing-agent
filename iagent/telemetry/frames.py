"""Telemetry frames: the unit shared by every source, the recorder and the rule engine."""

from dataclasses import dataclass
from typing import Mapping

# iRacing channels the pipeline relies on. Sources stream whichever of these exist (plus any
# extras requested); everything downstream must tolerate a missing channel.
CORE_CHANNELS: tuple[str, ...] = (
    "SessionTime",
    "SessionTick",
    "Lap",
    "LapDist",
    "LapDistPct",
    "Speed",
    "Throttle",
    "Brake",
    "Clutch",
    "SteeringWheelAngle",
    "Gear",
    "RPM",
    "LatAccel",
    "LongAccel",
    "VertAccel",
    "OnPitRoad",
    "IsOnTrack",
    "PlayerTrackSurface",
)

# Channels whose values are categories/flags: resampled by nearest sample, never interpolated.
DISCRETE_CHANNELS = frozenset(
    {"Lap", "Gear", "OnPitRoad", "IsOnTrack", "PlayerTrackSurface", "SessionTick"}
)

# Values of iRacing's `PlayerTrackSurface`.
SURFACE_NOT_IN_WORLD = -1
SURFACE_OFF_TRACK = 0
SURFACE_IN_PIT_STALL = 1
SURFACE_APPROACHING_PITS = 2
SURFACE_ON_TRACK = 3


@dataclass(frozen=True, slots=True)
class Frame:
    """One telemetry sample. Time is `SessionTime` (seconds), never the wall clock, so replays
    can run faster than real time and stay deterministic."""

    session_time: float
    values: Mapping[str, float]

    def get(self, key: str, default: float | None = None) -> float | None:
        return self.values.get(key, default)
