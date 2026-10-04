"""Lap summaries and lap-vs-reference comparison over equal distance sections.

Works on distance-gridded laps (`LapStore.load(..., grid=True)`). Sections are a stand-in until
the corner map exists (phase 3); the outputs are plain dicts so the CLI can emit them as JSON.
"""

from typing import Sequence

import numpy as np
import pandas as pd

MS_TO_KPH = 3.6
BRAKE_ON = 0.1  # Brake fraction that counts as "on the brakes"
FULL_THROTTLE = 0.98


def _times_at(grid: pd.DataFrame, distances: np.ndarray, lap_time: float | None) -> np.ndarray:
    """Elapsed lap time at each distance. The final boundary (the line) is the lap time itself."""
    times = np.interp(distances, grid["LapDist"], grid["lap_time_s"])
    if lap_time is not None:
        times[distances >= grid["LapDist"].iloc[-1]] = lap_time
    return times


def _boundaries(grid: pd.DataFrame, sections: int) -> np.ndarray:
    length = float(grid["LapDist"].iloc[-1]) + float(grid["LapDist"].diff().median())
    return np.linspace(0.0, length, sections + 1)


def _with_brake_onsets(grid: pd.DataFrame) -> pd.DataFrame:
    """Mark where braking *starts* (off → on), so a stop that began in an earlier section isn't
    reported as starting at this section's edge."""
    if "Brake" not in grid:
        return grid
    on = grid["Brake"] > BRAKE_ON
    return grid.assign(_brake_onset=on & ~on.shift(1, fill_value=False))


def _first_brake_m(part: pd.DataFrame) -> float | None:
    if "_brake_onset" not in part:
        return None
    onsets = part.loc[part["_brake_onset"], "LapDist"]
    return round(float(onsets.iloc[0]), 1) if len(onsets) else None


def _section_stats(part: pd.DataFrame) -> dict:
    return {
        "min_speed_kph": round(float(part["Speed"].min()) * MS_TO_KPH, 1) if "Speed" in part else None,
        "brake_start_m": _first_brake_m(part),
    }


def summarize(grid: pd.DataFrame, lap_time: float | None, sections: int = 10) -> dict:
    """Headline numbers and distance splits for one lap. `brake_start_m` is the first point in
    each section where braking begins (None if braking only continues from the previous one)."""
    grid = _with_brake_onsets(grid)
    out: dict = {}
    if "Speed" in grid:
        speed = grid["Speed"] * MS_TO_KPH
        out["top_speed_kph"] = round(float(speed.max()), 1)
        out["min_speed_kph"] = round(float(speed.min()), 1)
    if "Throttle" in grid:
        out["full_throttle_pct"] = round(float((grid["Throttle"] > FULL_THROTTLE).mean()) * 100, 1)
    if "Brake" in grid:
        out["braking_pct"] = round(float((grid["Brake"] > BRAKE_ON).mean()) * 100, 1)

    bounds = _boundaries(grid, sections)
    times = _times_at(grid, bounds, lap_time)
    out["splits"] = [
        {
            "section": i + 1,
            "start_m": round(float(bounds[i])),
            "end_m": round(float(bounds[i + 1])),
            "time_s": round(float(times[i + 1] - times[i]), 3),
            **_section_stats(grid[(grid["LapDist"] >= bounds[i]) & (grid["LapDist"] < bounds[i + 1])]),
        }
        for i in range(sections)
    ]
    return out


def compare(
    lap: pd.DataFrame,
    lap_time: float | None,
    ref: pd.DataFrame,
    ref_time: float | None,
    sections: int = 20,
) -> dict:
    """Where `lap` gains or loses time against `ref`, section by section.

    `delta_s` is positive when `lap` is slower than the reference in that section.
    """
    lap, ref = _with_brake_onsets(lap), _with_brake_onsets(ref)
    bounds = _boundaries(ref, sections)
    t_lap = _times_at(lap, bounds, lap_time)
    t_ref = _times_at(ref, bounds, ref_time)
    rows = []
    for i in range(sections):
        in_lap = lap[(lap["LapDist"] >= bounds[i]) & (lap["LapDist"] < bounds[i + 1])]
        in_ref = ref[(ref["LapDist"] >= bounds[i]) & (ref["LapDist"] < bounds[i + 1])]
        mine, theirs = _section_stats(in_lap), _section_stats(in_ref)
        rows.append({
            "section": i + 1,
            "start_m": round(float(bounds[i])),
            "end_m": round(float(bounds[i + 1])),
            "delta_s": round(float((t_lap[i + 1] - t_lap[i]) - (t_ref[i + 1] - t_ref[i])), 3),
            "min_speed_kph": mine["min_speed_kph"],
            "ref_min_speed_kph": theirs["min_speed_kph"],
            "brake_start_m": mine["brake_start_m"],
            "ref_brake_start_m": theirs["brake_start_m"],
        })
    total = None
    if lap_time is not None and ref_time is not None:
        total = round(lap_time - ref_time, 3)
    losses = sorted((r for r in rows if r["delta_s"] > 0), key=lambda r: -r["delta_s"])
    return {
        "total_delta_s": total,
        "biggest_losses": [r["section"] for r in losses[:3]],
        "sections": rows,
    }


def trace(
    grid: pd.DataFrame,
    channels: Sequence[str],
    start_m: float = 0.0,
    end_m: float | None = None,
    step_m: float = 10.0,
) -> pd.DataFrame:
    """Channel values every `step_m` metres between `start_m` and `end_m`. Speed is in km/h."""
    missing = [c for c in channels if c not in grid]
    if missing:
        raise KeyError(f"channels not in this lap: {missing}; available: {sorted(grid.columns)}")
    end = float(grid["LapDist"].iloc[-1]) if end_m is None else end_m
    part = grid[(grid["LapDist"] >= start_m) & (grid["LapDist"] <= end)]
    stride = max(1, int(round(step_m / float(grid["LapDist"].diff().median()))))
    out = part.iloc[::stride][["LapDist", "lap_time_s", *channels]].copy()
    if "Speed" in out:
        out["Speed"] = out["Speed"] * MS_TO_KPH
    return out.round(3).reset_index(drop=True)
