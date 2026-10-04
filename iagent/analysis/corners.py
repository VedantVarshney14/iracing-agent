"""Corner maps and per-corner metrics.

A corner map is derived from laps, not hand-made: a corner is a stretch of sustained lateral
acceleration in the median profile of representative laps. Thresholds are relative to the car's
own peak cornering grip, so the same code works for an F4 and a GT3. Names are not derived; they
are attached later (by the agent, the driver, or imported landmarks) and survive re-mapping.

The map also tiles the lap into one *segment* per corner, from the fastest point before its
braking zone to the fastest point before the next one, so per-corner time deltas add up to the
lap delta.

Distances are metres from the start/finish line, speeds km/h, on the 1 m distance grid.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from iagent.telemetry.frames import SURFACE_OFF_TRACK

MS_TO_KPH = 3.6
BRAKE_ON = 0.1
FULL_THROTTLE = 0.98

SMOOTH_M = 31  # lateral g is noisy (kerbs, bumps): smooth over ~30 m
ON_FRACTION = 0.45  # a corner peaks above this fraction of the car's peak lateral g ...
OFF_FRACTION = 0.2  # ... and extends while above this fraction
MERGE_GAP_M = 60  # same-direction parts closer than this are one corner (e.g. a double apex)
MIN_LENGTH_M = 40
FLAT_SPEED_DROP = 0.05  # a corner losing <5% of its entry speed is taken flat: apex = peak lateral g
FULL_THROTTLE_HOLD_M = 20  # throttle must stay full this long to count as "back on full throttle"
BRAKE_LOOKBACK_M = 50  # braking often starts a few metres before the speed peak at a segment start


@dataclass
class Corner:
    id: int
    direction: str  # "L" or "R"
    entry_m: float
    apex_m: float
    exit_m: float
    segment_start_m: float  # this corner's share of the lap (segments tile the lap)
    segment_end_m: float
    ref_min_speed_kph: float
    flat: bool  # taken without a meaningful speed drop
    name: str | None = None
    name_source: str | None = None  # e.g. "driver", "crewchief", "web", "model"
    name_confidence: str | None = None  # "high" | "medium" | "low"

    @property
    def label(self) -> str:
        return f"T{self.id} {self.name}" if self.name else f"T{self.id}"


@dataclass
class CornerMap:
    track_key: str
    car_key: str  # the car whose laps the map was derived from
    length_m: float
    derived_from: list[str]
    corners: list[Corner]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "CornerMap":
        raw = json.loads(text)
        raw["corners"] = [Corner(**c) for c in raw["corners"]]
        return cls(**raw)

    def get(self, corner_id: int) -> Corner:
        for c in self.corners:
            if c.id == corner_id:
                return c
        raise KeyError(f"No corner T{corner_id}; this map has T1-T{len(self.corners)}.")


def map_path(workspace: Path, track_key: str) -> Path:
    return workspace / "tracks" / track_key / "corners.json"


def load_map(workspace: Path, track_key: str) -> CornerMap | None:
    path = map_path(workspace, track_key)
    return CornerMap.from_json(path.read_text()) if path.exists() else None


def save_map(workspace: Path, cmap: CornerMap) -> Path:
    path = map_path(workspace, cmap.track_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cmap.to_json() + "\n")
    return path


# --- derivation -----------------------------------------------------------------------------


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, center=True, min_periods=1).mean().to_numpy()


def _median_profile(grids: Sequence[pd.DataFrame], column: str, smooth: int = 1) -> np.ndarray:
    n = min(len(g) for g in grids)
    rows = [g[column].to_numpy(dtype=float)[:n] for g in grids]
    if smooth > 1:
        rows = [_smooth(r, smooth) for r in rows]
    return np.nanmedian(np.vstack(rows), axis=0)


def _regions(lat: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [start, end] of corners in a smoothed lateral-g profile."""
    mag = np.abs(lat)
    peak = float(np.nanpercentile(mag, 98))
    on, off = ON_FRACTION * peak, OFF_FRACTION * peak

    # Hysteresis: grow each above-`on` point out to where it falls below `off`.
    above_off = mag > off
    regions: list[list[int]] = []
    i, n = 0, len(mag)
    while i < n:
        if mag[i] > on:
            start = i
            while start > 0 and above_off[start - 1]:
                start -= 1
            end = i
            while end < n - 1 and above_off[end + 1]:
                end += 1
            regions.append([start, end])
            i = end + 1
        else:
            i += 1

    # A change of direction inside a region is two corners (a chicane).
    parts: list[list[int]] = []
    for start, end in regions:
        sign = np.sign(lat[start : end + 1])
        cut = start
        for k in range(start + 1, end + 1):
            if sign[k - start] != 0 and sign[k - start] != sign[k - start - 1]:
                parts.append([cut, k - 1])
                cut = k
        parts.append([cut, end])

    # Same-direction parts separated by a short gap are one corner (Pouhon's double apex).
    merged: list[list[int]] = []
    for part in parts:
        if merged:
            prev = merged[-1]
            same_dir = np.sign(lat[prev[0] : prev[1] + 1].mean()) == np.sign(lat[part[0] : part[1] + 1].mean())
            if same_dir and part[0] - prev[1] < MERGE_GAP_M:
                prev[1] = part[1]
                continue
        merged.append(part)

    return [
        (start, end)
        for start, end in merged
        if end - start >= MIN_LENGTH_M and mag[start : end + 1].max() > on
    ]


def derive_corner_map(
    grids: Sequence[pd.DataFrame],
    track_key: str,
    car_key: str,
    lap_ids: Sequence[str],
) -> CornerMap:
    """Derive a corner map from distance-gridded laps of one track and car (ideally several
    representative laps; one works)."""
    if not grids:
        raise ValueError("Need at least one lap to derive a corner map.")
    if any("LatAccel" not in g for g in grids):
        raise ValueError("Laps lack the LatAccel channel; can't find corners.")
    lat = _median_profile(grids, "LatAccel", SMOOTH_M)
    speed = _median_profile(grids, "Speed") * MS_TO_KPH
    dist = grids[0]["LapDist"].to_numpy(dtype=float)[: len(lat)]
    step = float(np.median(np.diff(dist)))
    length = float(dist[-1] + step)

    spans = _regions(lat)
    apexes, flats = [], []
    for start, end in spans:
        entry_speed = float(np.nanmax(speed[max(0, start - 50) : start + 1]))
        v_min = float(np.nanmin(speed[start : end + 1]))
        flat = (entry_speed - v_min) < FLAT_SPEED_DROP * entry_speed
        inside = np.abs(lat[start : end + 1]) if flat else -speed[start : end + 1]
        apexes.append(start + int(np.nanargmax(inside)))
        flats.append(flat)

    # Segment boundaries: the fastest point between one corner's exit and the next one's apex,
    # i.e. where the next braking zone starts. On a plateau (top speed, limiter) take its last
    # point, so the straight belongs to the corner before it.
    bounds = [0.0]
    for k in range(len(spans) - 1):
        lo, hi = spans[k][1], apexes[k + 1]
        window = speed[lo : hi + 1]
        last_max = len(window) - 1 - int(np.nanargmax(window[::-1]))
        bounds.append(float(dist[lo + last_max]))
    bounds.append(length)

    corners = []
    for k, ((start, end), apex, flat) in enumerate(zip(spans, apexes, flats)):
        corners.append(Corner(
            id=k + 1,
            direction="L" if lat[start : end + 1].mean() > 0 else "R",  # iRacing: left is positive
            entry_m=float(dist[start]),
            apex_m=float(dist[apex]),
            exit_m=float(dist[end]),
            segment_start_m=bounds[k],
            segment_end_m=bounds[k + 1],
            ref_min_speed_kph=round(float(np.nanmin(speed[start : end + 1])), 1),
            flat=bool(flat),
        ))
    return CornerMap(track_key, car_key, length, list(lap_ids), corners)


def carry_names(old: CornerMap, new: CornerMap) -> CornerMap:
    """Keep names from a previous map for corners whose apex still falls inside the old corner."""
    for c in new.corners:
        for o in old.corners:
            if o.name and o.entry_m <= c.apex_m <= o.exit_m and o.direction == c.direction:
                c.name, c.name_source, c.name_confidence = o.name, o.name_source, o.name_confidence
                break
    return new


# --- metrics --------------------------------------------------------------------------------


def _at(grid: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    return grid[(grid["LapDist"] >= lo) & (grid["LapDist"] <= hi)]


def _time_at(grid: pd.DataFrame, d: float, lap_time: float | None, length: float) -> float:
    """Elapsed time at distance `d`. For a complete lap the line itself is exactly 0 and
    `lap_time` (the grid's first and last points sit just inside the line)."""
    if lap_time is not None and d <= 0.0:
        return 0.0
    if lap_time is not None and d >= length - 1e-6:
        return lap_time
    return float(np.interp(d, grid["LapDist"], grid["lap_time_s"]))


def _r(x, nd: int = 1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def corner_metrics(grid: pd.DataFrame, lap_time: float | None, cmap: CornerMap) -> list[dict]:
    """Per-corner numbers for one lap.

    - `time_s`: time through the corner's segment (segments tile the lap).
    - `brake_m`: where braking began (first brake onset from just before the segment start, but
      after the previous corner, up to the apex); None if the corner was taken without braking
      (see `min_throttle` for a lift).
    - `entry_speed_kph`: fastest point of the segment before the apex.
    - `min_speed_kph` / `min_speed_m`: slowest point in the corner.
    - `full_throttle_m`: first point after the slowest point where the throttle is full and stays
      full for 20 m; None if that never happens within the segment.
    - `exit_speed_kph`: speed where the corner ends (`exit_m` in the map).
    - `off_track_m`: metres of the segment driven off the track surface (an incident: the other
      numbers for this corner then describe the incident, not the driver's normal technique).
    """
    on = grid["Brake"] > BRAKE_ON if "Brake" in grid else pd.Series(False, index=grid.index)
    onset = on & ~on.shift(1, fill_value=False)
    full = grid["Throttle"] >= FULL_THROTTLE if "Throttle" in grid else None
    step = float(grid["LapDist"].diff().median())
    hold = max(1, int(round(FULL_THROTTLE_HOLD_M / step)))

    out = []
    prev_exit = 0.0
    for c in cmap.corners:
        approach = _at(grid, max(prev_exit, c.segment_start_m - BRAKE_LOOKBACK_M), c.apex_m)
        prev_exit = c.exit_m
        corner = _at(grid, c.entry_m, c.exit_m)
        if corner["Speed"].isna().all():
            out.append({"corner": c.id, "name": c.name, "missing": True})
            continue

        onsets = approach.index[onset.loc[approach.index]]
        brake_m = float(grid.loc[onsets[0], "LapDist"]) if len(onsets) else None
        imin = corner["Speed"].idxmin()
        min_speed_m = float(grid.loc[imin, "LapDist"])

        full_throttle_m = None
        if full is not None:
            after = full.loc[imin : grid.index[grid["LapDist"] <= c.segment_end_m][-1]]
            run = after.astype(int).rolling(hold, min_periods=hold).sum() >= hold
            if run.any():
                full_throttle_m = float(grid.loc[run.idxmax(), "LapDist"]) - (hold - 1) * step

        exit_row = grid.iloc[(grid["LapDist"] - c.exit_m).abs().argmin()]
        segment = _at(grid, c.segment_start_m, c.segment_end_m)
        off_m = None
        if "PlayerTrackSurface" in segment:
            off_m = round(float((segment["PlayerTrackSurface"] == SURFACE_OFF_TRACK).sum()) * step)
        out.append({
            "corner": c.id,
            "name": c.name,
            "time_s": _r(_time_at(grid, c.segment_end_m, lap_time, cmap.length_m)
                         - _time_at(grid, c.segment_start_m, lap_time, cmap.length_m), 3),
            "brake_m": _r(brake_m, 0),
            "brake_peak": _r(approach["Brake"].max(), 2) if "Brake" in approach else None,
            "min_throttle": _r(approach["Throttle"].min(), 2) if "Throttle" in approach else None,
            "entry_speed_kph": _r(approach["Speed"].max() * MS_TO_KPH),
            "min_speed_kph": _r(corner["Speed"].min() * MS_TO_KPH),
            "min_speed_m": _r(min_speed_m, 0),
            "full_throttle_m": _r(full_throttle_m, 0),
            "exit_speed_kph": _r(exit_row["Speed"] * MS_TO_KPH),
            "min_gear": int(corner["Gear"].min()) if "Gear" in corner else None,
            "off_track_m": off_m,
        })
    return out


def _diff(a, b, nd=1):
    return None if a is None or b is None else round(a - b, nd)


def compare_corners(lap: list[dict], ref: list[dict]) -> list[dict]:
    """Corner-by-corner differences. Signs read naturally for a driver:

    - `delta_s` > 0: slower than the reference through this corner.
    - `brake_diff_m` > 0: braked *later* (further down the road) than the reference.
    - `min_speed_diff_kph` > 0: carried more speed.
    - `full_throttle_diff_m` > 0: back on full throttle *later*.
    - `exit_speed_diff_kph` > 0: faster on exit.
    """
    rows = []
    for a, b in zip(lap, ref):
        if a.get("missing") or b.get("missing"):
            rows.append({"corner": a["corner"], "name": a["name"], "missing": True})
            continue
        rows.append({
            "corner": a["corner"],
            "name": a["name"],
            "delta_s": _diff(a["time_s"], b["time_s"], 3),
            "brake_m": a["brake_m"],
            "ref_brake_m": b["brake_m"],
            "brake_diff_m": _diff(a["brake_m"], b["brake_m"], 0),
            "min_speed_kph": a["min_speed_kph"],
            "ref_min_speed_kph": b["min_speed_kph"],
            "min_speed_diff_kph": _diff(a["min_speed_kph"], b["min_speed_kph"]),
            "full_throttle_m": a["full_throttle_m"],
            "ref_full_throttle_m": b["full_throttle_m"],
            "full_throttle_diff_m": _diff(a["full_throttle_m"], b["full_throttle_m"], 0),
            "exit_speed_diff_kph": _diff(a["exit_speed_kph"], b["exit_speed_kph"]),
            "off_track_m": a["off_track_m"],
            "ref_off_track_m": b["off_track_m"],
        })
    return rows


def consistency(per_lap: Sequence[list[dict]]) -> list[dict]:
    """Spread across laps, per corner: how repeatable the driver is. Lower is better.

    `*_spread` values are standard deviations; `*_mean` the average across laps."""
    if not per_lap:
        return []
    rows = []
    for i, first in enumerate(per_lap[0]):
        laps = [lap[i] for lap in per_lap if not lap[i].get("missing")]

        def stats(key: str, nd: int = 1):
            vals = [lap[key] for lap in laps if lap.get(key) is not None]
            if len(vals) < 2:
                return None, None
            return round(float(np.mean(vals)), nd), round(float(np.std(vals)), nd)

        brake_mean, brake_spread = stats("brake_m", 0)
        speed_mean, speed_spread = stats("min_speed_kph")
        thr_mean, thr_spread = stats("full_throttle_m", 0)
        time_mean, time_spread = stats("time_s", 3)
        times = [lap["time_s"] for lap in laps if lap.get("time_s") is not None]
        rows.append({
            "corner": first["corner"],
            "name": first["name"],
            "laps": len(laps),
            "time_mean_s": time_mean,
            "time_spread_s": time_spread,
            "best_time_s": round(min(times), 3) if times else None,
            "brake_mean_m": brake_mean,
            "brake_spread_m": brake_spread,
            "min_speed_mean_kph": speed_mean,
            "min_speed_spread_kph": speed_spread,
            "full_throttle_mean_m": thr_mean,
            "full_throttle_spread_m": thr_spread,
        })
    return rows
