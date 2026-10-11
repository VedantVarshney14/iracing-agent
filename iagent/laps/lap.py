"""A lap: where and in what it was driven, how long it took, whether it can be used, and (when
loaded) its samples."""

from dataclasses import dataclass, field, replace

import pandas as pd

from iagent.telemetry.session import SessionInfo

# Structural reasons a lap can't be used at all. A lap is `valid` only if it has none.
# Off-track time is deliberately *not* one: a brief clip of the kerb is normal, and whether a lap
# is representative is a question of pace (see `iagent.laps.pace`), not of track-surface flags.
REASON_INCOMPLETE = "incomplete"  # didn't both start and end on a start/finish crossing
REASON_PIT_ROAD = "pit_road"
REASON_DISCONTINUITY = "discontinuity"  # position jumped (reset / tow / rewind)


@dataclass(frozen=True)
class Lap:
    """The same type whether it was just segmented (with `frames`) or listed from a lap store
    (without: `LapStore.load` gives the samples)."""

    lap_id: str
    session_id: str
    track_key: str  # grouping key, e.g. "spa-2024-up" (see SessionInfo.track_key)
    car_key: str  # grouping key, e.g. "formulair04"
    track: str  # display name
    car: str  # display name
    track_length_m: float | None  # None only for laps stored before it was recorded
    seq: int  # 0-based order within the session
    lap_time: float | None  # exact (interpolated across the line); None unless complete
    reasons: tuple[str, ...] = ()
    sim_lap: int | None = None  # iRacing's own `Lap` counter, when the channel exists
    off_track_s: float = 0.0  # time with the car off the track surface (information only)
    source: str = "unknown"  # e.g. "ibt", "garage61"
    # Raw samples, including `lap_time_s` and `LapDist` columns.
    frames: pd.DataFrame | None = field(default=None, repr=False, compare=False)

    @classmethod
    def in_session(cls, session: SessionInfo, seq: int, **kw) -> "Lap":
        return cls(
            lap_id=f"{session.session_id}-L{seq:03d}",
            session_id=session.session_id,
            track_key=session.track_key,
            car_key=session.car_key,
            track=session.track_name,
            car=session.car_name,
            track_length_m=session.track_length_m,
            seq=seq,
            **kw,
        )

    @property
    def complete(self) -> bool:
        return REASON_INCOMPLETE not in self.reasons

    @property
    def valid(self) -> bool:
        return not self.reasons

    def without_frames(self) -> "Lap":
        return replace(self, frames=None)
