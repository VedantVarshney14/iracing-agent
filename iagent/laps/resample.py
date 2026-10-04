"""Resample a lap onto a fixed distance grid so laps can be compared point for point."""

import numpy as np
import pandas as pd

from iagent.laps.segment import Lap
from iagent.telemetry.frames import DISCRETE_CHANNELS


def resample_to_distance(lap: Lap, step_m: float = 1.0) -> pd.DataFrame:
    """One row per `step_m` metres from the start/finish line.

    `LapDist` is the grid, `lap_time_s` is the elapsed time at each point (so time deltas between
    laps are a subtraction). Continuous channels are linearly interpolated, discrete ones (gear,
    flags) take the nearest sample. For incomplete laps the grid outside the driven range is NaN.
    """
    df = lap.frames
    length = lap.session.track_length_m
    grid = np.arange(0.0, length, step_m)

    # Distance must be non-decreasing to interpolate against it.
    dist = np.maximum.accumulate(df["LapDist"].to_numpy(dtype=float))

    if lap.complete:
        left = right = None  # clamp to the edge samples
    else:
        left = right = np.nan

    out: dict[str, np.ndarray] = {"LapDist": grid}
    for col in df.columns:
        if col in ("LapDist", "LapDistPct"):
            continue
        values = df[col].to_numpy(dtype=float)
        if col in DISCRETE_CHANNELS:
            out[col] = _nearest(grid, dist, values, valid_range=lap.complete is False)
        else:
            out[col] = np.interp(grid, dist, values, left=left, right=right)
    out["LapDistPct"] = grid / length
    return pd.DataFrame(out)


def _nearest(grid: np.ndarray, dist: np.ndarray, values: np.ndarray, valid_range: bool) -> np.ndarray:
    idx = np.searchsorted(dist, grid)
    lo = np.clip(idx - 1, 0, len(dist) - 1)
    hi = np.clip(idx, 0, len(dist) - 1)
    pick = np.where(np.abs(dist[lo] - grid) <= np.abs(dist[hi] - grid), lo, hi)
    res = values[pick].astype(float)
    if valid_range:
        res[(grid < dist[0]) | (grid > dist[-1])] = np.nan
    return res
