"""Synthetic telemetry with exact ground truth.

A parametric circuit (a few corners on a straight-ish lap) is driven with a deterministic speed
profile, with per-lap jitter in brake points and apex speeds, at 60 Hz. Tests and the corner
mapper can then assert against known answers instead of "doesn't crash".

Laps can be made messy on purpose (`LapKind`) to exercise segmentation and validity logic.
"""

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

import numpy as np

from iagent.telemetry.frames import (
    Frame,
    SURFACE_OFF_TRACK,
    SURFACE_ON_TRACK,
)
from iagent.telemetry.session import SessionInfo

HZ = 60
GRID_M = 0.5  # profile resolution
V_MAX = 60.0  # m/s (~216 km/h)
A_ACCEL = 6.0  # m/s^2 out of corners
A_BRAKE_MAX = 15.0  # m/s^2 -> brake pedal = decel / A_BRAKE_MAX
PICKUP_M = 15.0  # throttle applied this far after the apex
LAT_PEAK = 18.0  # m/s^2 of lateral acceleration at each apex
CORNER_WIDTH_M = 60.0  # how quickly curvature fades either side of an apex
SLOW_FACTOR = 1.15


@dataclass(frozen=True)
class SyntheticCorner:
    name: str
    apex_m: float
    brake_m: float  # nominal distance at which braking starts
    min_speed: float  # m/s at the apex
    direction: int = 1  # +1 right, -1 left (channels follow iRacing: left turns are positive)


@dataclass(frozen=True)
class SyntheticTrack:
    name: str
    length_m: float
    corners: tuple[SyntheticCorner, ...]


DEFAULT_TRACK = SyntheticTrack(
    name="Synthetic Test Circuit",
    length_m=3000.0,
    corners=(
        SyntheticCorner("T1", apex_m=600, brake_m=480, min_speed=28.0, direction=1),
        SyntheticCorner("T2", apex_m=1200, brake_m=1080, min_speed=32.0, direction=-1),
        SyntheticCorner("T3", apex_m=1900, brake_m=1770, min_speed=25.0, direction=1),
        SyntheticCorner("T4", apex_m=2500, brake_m=2390, min_speed=30.0, direction=-1),
    ),
)


class LapKind(str, Enum):
    CLEAN = "clean"
    OFF_TRACK = "off_track"  # excursion mid-lap
    PIT_IN = "pit_in"  # lap ends on pit road
    OUT_LAP = "out_lap"  # lap starts on pit road
    RESET = "reset"  # car teleported back mid-lap
    SLOW = "slow"  # a structurally fine lap, driven `SLOW_FACTOR` times slower (a spin recovery, say)


@dataclass
class LapTruth:
    """What the generator actually did on a lap."""

    index: int
    kind: LapKind
    lap_time: float
    start_time: float  # session time of the start/finish crossing
    brake_m: dict[str, float]  # absolute distance where braking started, per corner
    min_speed: dict[str, float]
    expect_valid: bool


@dataclass
class _Profile:
    d: np.ndarray
    v: np.ndarray
    brake: np.ndarray
    throttle: np.ndarray
    steer: np.ndarray
    lat_accel: np.ndarray
    long_accel: np.ndarray
    t: np.ndarray  # cumulative time at each grid point
    truth_brake: dict[str, float] = field(default_factory=dict)
    truth_min_speed: dict[str, float] = field(default_factory=dict)


def build_profile(
    track: SyntheticTrack,
    brake_offsets: dict[str, float] | None = None,
    speed_offsets: dict[str, float] | None = None,
) -> _Profile:
    """Speed profile along the lap. A positive brake offset means braking *later* (closer to the
    apex), which forces harder braking to still make the apex speed."""
    brake_offsets = brake_offsets or {}
    speed_offsets = speed_offsets or {}
    d = np.arange(0.0, track.length_m + GRID_M, GRID_M)
    v = np.full_like(d, V_MAX)
    brake = np.zeros_like(d)
    steer = np.zeros_like(d)
    curvature = np.zeros_like(d)  # signed 1/m, positive = left
    truth_brake: dict[str, float] = {}
    truth_min: dict[str, float] = {}

    for c in track.corners:
        brake_d = c.brake_m + brake_offsets.get(c.name, 0.0)
        v_min = c.min_speed + speed_offsets.get(c.name, 0.0)
        truth_brake[c.name] = brake_d
        truth_min[c.name] = v_min
        decel = (V_MAX**2 - v_min**2) / (2.0 * (c.apex_m - brake_d))
        if decel > A_BRAKE_MAX:
            raise ValueError(f"{c.name}: required decel {decel:.1f} m/s^2 exceeds the car's limit.")

        pre = (d >= brake_d) & (d <= c.apex_m)
        v_pre = np.sqrt(np.maximum(V_MAX**2 - 2.0 * decel * (d - brake_d), v_min**2))
        v = np.where(pre, np.minimum(v, v_pre), v)
        brake = np.where(pre, np.maximum(brake, decel / A_BRAKE_MAX), brake)

        post = d > c.apex_m
        v_post = np.sqrt(v_min**2 + 2.0 * A_ACCEL * np.maximum(d - c.apex_m, 0.0))
        v = np.where(post, np.minimum(v, v_post), v)

        shape = np.exp(-(((d - c.apex_m) / CORNER_WIDTH_M) ** 2))
        steer -= c.direction * 2.0 * shape  # iRacing: steering and lateral g are negative to the right
        curvature -= c.direction * (LAT_PEAK / v_min**2) * shape

    dt = GRID_M / np.maximum((v[:-1] + v[1:]) / 2.0, 1e-3)
    t = np.concatenate([[0.0], np.cumsum(dt)])

    # Throttle: off while braking and until the pick-up point after each apex, full otherwise.
    throttle = np.where(brake > 0.0, 0.0, 1.0)
    for c in track.corners:
        throttle = np.where((d > c.apex_m - 30) & (d < c.apex_m + PICKUP_M), 0.0, throttle)

    long_accel = np.gradient(v, t) if len(t) > 1 else np.zeros_like(v)
    lat_accel = v**2 * curvature
    return _Profile(d, v, brake, throttle, steer, lat_accel, long_accel, t, truth_brake, truth_min)


class SyntheticSource:
    """Generates `n_laps` consecutive laps at 60 Hz. Deterministic for a given seed.

    Args:
        kinds: lap kind per lap (defaults to all clean).
        brake_jitter_m / speed_jitter: std devs of per-lap brake-point / apex-speed variation.
        start_m: begin the session part-way round the first lap (a partial first lap).
    """

    def __init__(
        self,
        track: SyntheticTrack = DEFAULT_TRACK,
        n_laps: int = 5,
        kinds: list[LapKind] | None = None,
        seed: int = 0,
        brake_jitter_m: float = 4.0,
        speed_jitter: float = 0.5,
        start_m: float = 0.0,
    ):
        self._track = track
        self._n_laps = n_laps
        self._kinds = kinds or [LapKind.CLEAN] * n_laps
        if len(self._kinds) != n_laps:
            raise ValueError("`kinds` must have one entry per lap.")
        self._seed = seed
        self._brake_jitter = brake_jitter_m
        self._speed_jitter = speed_jitter
        self._start_m = start_m
        self.truth: list[LapTruth] = []
        self._session = SessionInfo(
            track_name=track.name,
            track_length_m=track.length_m,
            car_name="Synthetic Car",
            session_id=f"synthetic-{seed}",
            track_code="synthetic",
            car_path="synthcar",
            track_id=9999,
            car_id=999,
        )

    @property
    def session(self) -> SessionInfo:
        return self._session

    def frames(self) -> Iterator[Frame]:
        rng = random.Random(self._seed)
        length = self._track.length_m
        clock = 0.0  # session time of the next frame
        self.truth = []
        lap_end = 0.0

        for i, kind in enumerate(self._kinds):
            profile = build_profile(
                self._track,
                {c.name: rng.gauss(0.0, self._brake_jitter) for c in self._track.corners},
                {c.name: rng.gauss(0.0, self._speed_jitter) for c in self._track.corners},
            )
            if kind is LapKind.SLOW:
                profile.v /= SLOW_FACTOR
                profile.long_accel /= SLOW_FACTOR**2
                profile.t *= SLOW_FACTOR
            lap_time = float(profile.t[-1])
            first_d = self._start_m if i == 0 else 0.0
            # Session time at which this lap's start/finish crossing happens.
            # Later laps start exactly where the previous one ended, so the crossing falls
            # between frames like it does in real telemetry.
            t_start = (
                clock - float(np.interp(first_d, profile.d, profile.t)) if i == 0 else lap_end
            )
            self.truth.append(
                LapTruth(
                    index=i,
                    kind=kind,
                    lap_time=lap_time,
                    start_time=t_start,
                    brake_m=profile.truth_brake,
                    min_speed=profile.truth_min_speed,
                    # Structural validity: off-track and slow laps are still usable laps. The
                    # first lap of a session never has an observed start/finish crossing, so it
                    # can't be a timed lap whatever `start_m` is.
                    expect_valid=kind in (LapKind.CLEAN, LapKind.SLOW, LapKind.OFF_TRACK) and i > 0,
                )
            )

            reset_done = False
            while clock - t_start < lap_time:
                d = float(np.interp(clock - t_start, profile.t, profile.d))
                if kind is LapKind.RESET and not reset_done and d >= 0.5 * length:
                    # Teleport back: rewind the lap timeline 20% of the lap's distance.
                    t_start += float(
                        np.interp(d, profile.d, profile.t)
                        - np.interp(0.05 * length, profile.d, profile.t)
                    )
                    reset_done = True
                    self.truth[-1].lap_time = float("nan")
                    continue
                yield self._frame(i, kind, profile, d, clock)
                clock += 1.0 / HZ
            lap_end = t_start + lap_time

        # One frame just past the line so the final lap gets its closing crossing.
        t_next = clock - lap_end
        yield self._frame(len(self._kinds), LapKind.CLEAN, profile, t_next * V_MAX, clock)

    def _frame(self, lap_idx: int, kind: LapKind, p: _Profile, d: float, clock: float) -> Frame:
        length = self._track.length_m
        surface = SURFACE_ON_TRACK
        on_pit = 0.0
        if kind is LapKind.OFF_TRACK and 1500.0 <= d <= 1560.0:
            surface = SURFACE_OFF_TRACK
        if kind is LapKind.PIT_IN and d >= length - 250.0:
            on_pit = 1.0
        if kind is LapKind.OUT_LAP and d <= 300.0:
            on_pit = 1.0
        speed = float(np.interp(d, p.d, p.v))
        return Frame(
            session_time=clock,
            values={
                "SessionTime": clock,
                "SessionTick": float(round(clock * HZ)),
                "Lap": float(lap_idx + 1),
                "LapDist": d,
                "LapDistPct": d / length,
                "Speed": speed,
                "Throttle": float(np.interp(d, p.d, p.throttle)),
                "Brake": float(np.interp(d, p.d, p.brake)),
                "SteeringWheelAngle": float(np.interp(d, p.d, p.steer)),
                "LatAccel": float(np.interp(d, p.d, p.lat_accel)),
                "LongAccel": float(np.interp(d, p.d, p.long_accel)),
                "Gear": float(min(6, 1 + int(speed // 10))),
                "OnPitRoad": on_pit,
                "IsOnTrack": 1.0,
                "PlayerTrackSurface": float(surface),
            },
        )


__all__ = [
    "DEFAULT_TRACK",
    "LapKind",
    "LapTruth",
    "SyntheticCorner",
    "SyntheticSource",
    "SyntheticTrack",
    "build_profile",
]
