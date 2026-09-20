"""Lap segmentation: turn a stream of frames into laps with exact times and validity flags."""

from dataclasses import dataclass, field

import pandas as pd

from iagent.common.frames import Frame, SURFACE_OFF_TRACK
from iagent.common.session import SessionInfo

# Reasons a lap can be flagged. A lap is `valid` only if it has none.
REASON_INCOMPLETE = "incomplete"  # didn't both start and end on a start/finish crossing
REASON_PIT_ROAD = "pit_road"
REASON_OFF_TRACK = "off_track"
REASON_DISCONTINUITY = "discontinuity"  # position jumped (reset / tow / rewind)


@dataclass
class Lap:
    session: SessionInfo
    seq: int  # 0-based order within the session
    frames: pd.DataFrame  # raw samples; includes `lap_time_s` and `LapDist` columns
    start_time: float | None  # session time of the start/finish crossing that began the lap
    end_time: float | None
    reasons: list[str] = field(default_factory=list)
    sim_lap: int | None = None  # iRacing's own `Lap` counter, when the channel exists

    @property
    def complete(self) -> bool:
        return REASON_INCOMPLETE not in self.reasons

    @property
    def valid(self) -> bool:
        return not self.reasons

    @property
    def lap_time(self) -> float | None:
        """Exact time (interpolated across the crossing frames). Only defined for complete laps."""
        if self.start_time is None or self.end_time is None or not self.complete:
            return None
        return self.end_time - self.start_time

    @property
    def lap_id(self) -> str:
        return f"{self.session.session_id}-L{self.seq:03d}"


class LapSegmenter:
    """Feed frames in order; a finished lap is returned when the start/finish line is crossed.

    A crossing is `LapDistPct` wrapping from near 1 to near 0. Any other large jump in position
    (reset, tow, rewind) flags the lap as discontinuous rather than starting a new one.
    """

    def __init__(
        self,
        session: SessionInfo,
        wrap_high: float = 0.9,
        wrap_low: float = 0.1,
        jump: float = 0.1,
        min_tail_frames: int = 60,
    ):
        self._session = session
        self._wrap_high = wrap_high
        self._wrap_low = wrap_low
        self._jump = jump
        self._min_tail_frames = min_tail_frames

        self._rows: list[dict[str, float]] = []
        self._start_time: float | None = None
        self._discontinuity = False
        self._prev: Frame | None = None
        self._seq = 0

    def push(self, frame: Frame) -> Lap | None:
        pct = frame.get("LapDistPct")
        if pct is None:
            raise ValueError("Frame has no LapDistPct; cannot segment laps.")
        if pct < 0.0:
            return None  # not in the world (garage, loading): no position to segment on

        finished: Lap | None = None
        prev = self._prev
        if prev is not None:
            prev_pct = prev.values["LapDistPct"]
            if prev_pct > self._wrap_high and pct < self._wrap_low:
                # Where in the gap between the two frames did the line get crossed?
                to_line = 1.0 - prev_pct
                frac = to_line / (to_line + pct) if (to_line + pct) > 0 else 1.0
                t_cross = prev.session_time + frac * (frame.session_time - prev.session_time)
                finished = self._finish(end_time=t_cross)
                self._start_time = t_cross
            elif abs(pct - prev_pct) > self._jump:
                self._discontinuity = True

        self._rows.append(dict(frame.values))
        self._prev = frame
        return finished

    def flush(self) -> Lap | None:
        """The trailing partial lap at the end of the stream (always flagged incomplete).

        Stubs shorter than `min_tail_frames` (e.g. the first frames after the last crossing) are
        dropped rather than saved as junk laps.
        """
        if len(self._rows) < self._min_tail_frames:
            self._rows = []
            return None
        return self._finish(end_time=None)

    def _finish(self, end_time: float | None) -> Lap | None:
        if not self._rows:
            return None
        df = pd.DataFrame(self._rows)
        if "LapDist" not in df:
            df["LapDist"] = df["LapDistPct"] * self._session.track_length_m
        origin = self._start_time if self._start_time is not None else df["SessionTime"].iloc[0]
        df["lap_time_s"] = df["SessionTime"] - origin

        reasons: list[str] = []
        if self._start_time is None or end_time is None:
            reasons.append(REASON_INCOMPLETE)
        if self._discontinuity:
            reasons.append(REASON_DISCONTINUITY)
        if "OnPitRoad" in df and (df["OnPitRoad"] > 0.5).any():
            reasons.append(REASON_PIT_ROAD)
        if "PlayerTrackSurface" in df and (df["PlayerTrackSurface"] == SURFACE_OFF_TRACK).any():
            reasons.append(REASON_OFF_TRACK)

        sim_lap = int(df["Lap"].median()) if "Lap" in df else None
        lap = Lap(
            session=self._session,
            seq=self._seq,
            frames=df,
            start_time=self._start_time,
            end_time=end_time,
            reasons=reasons,
            sim_lap=sim_lap,
        )
        self._seq += 1
        self._rows = []
        self._discontinuity = False
        return lap
