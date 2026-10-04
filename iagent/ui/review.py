"""What the web UI shows, built from the workspace as plain JSON-ready dicts.

Kept apart from the HTTP layer so it can be tested (and reused, e.g. by the coach) directly.
"""

import dataclasses
import math

import numpy as np
import pandas as pd

from iagent.analysis.compare import MS_TO_KPH
from iagent.analysis.corners import compare_corners, corner_metrics
from iagent.analysis.position import has_position, line_offset, to_local_xy
from iagent.laps.pace import best_times, group_of, representative
from iagent.laps.store import LapRecord
from iagent.workspace import Workspace, WorkspaceError

# Grid channels sent to the UI, and how to scale them for display.
TRACE_CHANNELS = {
    "Speed": MS_TO_KPH,  # km/h
    "Throttle": 100.0,  # %
    "Brake": 100.0,  # %
    "Gear": 1.0,
    "SteeringWheelAngle": 180.0 / math.pi,  # degrees
}


def tracks(ws: Workspace) -> list[dict]:
    """Track/car combinations with lap counts and best times, most recently driven first."""
    if not (ws.root / "index.sqlite").exists():
        return []
    records = ws.store().list()
    best = best_times(records)
    groups: dict[tuple[str, str], list[LapRecord]] = {}
    for r in records:
        groups.setdefault(group_of(r), []).append(r)
    out = []
    for (track, car), recs in groups.items():
        refs = ws.refs()
        out.append({
            "track": track,
            "car": car,
            "track_name": recs[0].track,
            "car_name": recs[0].car,
            "laps": len(recs),
            "valid_laps": sum(r.valid for r in recs),
            "best_lap_time": best.get((track, car)),
            "last_session": max(r.session_id for r in recs),
            "reference_laps": len(refs.list(track=track, car=car)) if refs else 0,
        })
    return sorted(out, key=lambda row: row["last_session"], reverse=True)


def laps(ws: Workspace, track: str, car: str) -> dict:
    """The driver's laps and the reference laps for one track and car, plus the defaults the
    review screen opens with."""
    own = ws.store().list(track=track, car=car)
    if not own:
        raise WorkspaceError(f"No laps for {track} / {car}.")
    best = best_times(own).get((track, car))
    rep_ids = {r.lap_id for r in representative(own)}
    refs = _references(ws, track, car)
    timed = [r for r in own if r.valid and r.lap_time is not None]
    default_lap = min(timed, key=lambda r: r.lap_time) if timed else None
    ghost = default_ghost(ws, default_lap) if default_lap else None
    return {
        "track": track,
        "car": car,
        "track_name": own[0].track,
        "car_name": own[0].car,
        "laps": [{
            "lap_id": r.lap_id,
            "session_id": r.session_id,
            "seq": r.seq,
            "lap_time": r.lap_time,
            "valid": r.valid,
            "representative": r.lap_id in rep_ids,
            "reasons": list(r.reasons),
            "off_track_s": r.off_track_s,
            "vs_best_pct": round((r.lap_time / best - 1) * 100, 2) if best and r.valid and r.lap_time else None,
        } for r in own],
        "references": refs,
        "default_lap": default_lap.lap_id if default_lap else None,
        "default_ghost": ghost.lap_id if ghost else None,
    }


def _references(ws: Workspace, track: str, car: str) -> list[dict]:
    store = ws.refs()
    records = store.list(track=track, car=car) if store else []
    rows = []
    for r in sorted(records, key=lambda r: r.lap_time or math.inf):
        meta = ws.ref_meta(r.lap_id)
        rows.append({
            "lap_id": r.lap_id,
            "lap_time": r.lap_time,
            "valid": r.valid,
            "driver": meta.get("driver"),
            "date": meta.get("date"),
            "source": r.source,
        })
    return rows


def default_ghost(ws: Workspace, rec: LapRecord) -> LapRecord | None:
    """The lap to compare against when none is chosen: the fastest valid reference lap (a
    Garage61 teammate's), otherwise the driver's fastest other valid lap."""
    store = ws.refs()
    refs = [r for r in (store.list(track=rec.track_key, car=rec.car_key) if store else [])
            if r.valid and r.lap_time is not None]
    if refs:
        return min(refs, key=lambda r: r.lap_time)
    try:
        return ws.reference(rec, None)
    except WorkspaceError:
        return None


def review(ws: Workspace, lap_id: str, ref_id: str | None = None, step_m: float = 2.0) -> dict:
    """Everything the lap review screen draws: both laps' traces on a shared distance grid, the
    time gap along the lap, the corner comparison and (when recorded) both racing lines."""
    rec = ws.find(lap_id)
    ref = ws.reference(rec, ref_id) if ref_id else default_ghost(ws, rec)
    if ref is None:
        raise WorkspaceError(f"No lap to compare {lap_id} against: drive another valid lap or import a reference lap.")
    cmap = ws.corner_map(rec.track_key)
    lap_grid, ref_grid = ws.load(rec.lap_id), ws.load(ref.lap_id)

    rows = compare_corners(
        corner_metrics(lap_grid, rec.lap_time, cmap),
        corner_metrics(ref_grid, ref.lap_time, cmap),
    )
    by_id = {row["corner"]: row for row in rows}
    corners = [{**dataclasses.asdict(c), "label": c.label, **by_id.get(c.id, {})} for c in cmap.corners]

    dist = np.arange(0.0, cmap.length_m, step_m)
    a, b = _on(lap_grid, dist), _on(ref_grid, dist)
    gap = a["lap_time_s"] - b["lap_time_s"]

    position, offset = None, None
    if has_position(lap_grid) and has_position(ref_grid):
        origin = (float(np.nanmean(b["Lat"])), float(np.nanmean(b["Lon"])))
        lx, ly = to_local_xy(a["Lat"], a["Lon"], origin)
        rx, ry = to_local_xy(b["Lat"], b["Lon"], origin)
        offset, nearest = line_offset(lx, ly, rx, ry, search=max(1, int(60 / step_m)))
        position = {
            "lap": {"x": _list(lx, 2), "y": _list(ly, 2)},
            "ref": {"x": _list(rx, 2), "y": _list(ry, 2)},
            "ref_index": [int(i) for i in nearest],  # the ghost's point nearest each of yours
        }

    return {
        "track": {"key": rec.track_key, "name": rec.track, "car": rec.car_key, "car_name": rec.car,
                  "length_m": cmap.length_m},
        "lap": _lap_info(ws, rec),
        "ref": _lap_info(ws, ref),
        "total_delta_s": round(rec.lap_time - ref.lap_time, 3) if rec.lap_time and ref.lap_time else None,
        "corners": _clean(corners),
        "trace": {
            "distance_m": _list(dist, 1),
            "gap_s": _list(gap, 3),
            # Your line's distance from the ghost's (m), + to the left of its direction of travel.
            "offset_m": _list(offset, 2) if offset is not None else None,
            "lap": _channels(a),
            "ref": _channels(b),
        },
        "position": position,
    }


def _lap_info(ws: Workspace, rec: LapRecord) -> dict:
    meta = ws.ref_meta(rec.lap_id)
    return {
        "lap_id": rec.lap_id,
        "session_id": rec.session_id,
        "seq": rec.seq,
        "lap_time": rec.lap_time,
        "reference": rec.source == "garage61",
        "driver": meta.get("driver"),
        "garage61_id": meta.get("garage61_id"),
        "ghost_available": meta.get("ghost_available"),
    }


def _on(grid: pd.DataFrame, dist: np.ndarray) -> dict[str, np.ndarray]:
    """A lap's channels sampled at `dist` (laps from different sources may use other grids)."""
    d = grid["LapDist"].to_numpy(dtype=float)
    out = {}
    for col in ("lap_time_s", "Lat", "Lon", *TRACE_CHANNELS):
        if col in grid:
            out[col] = np.interp(dist, d, grid[col].to_numpy(dtype=float), left=np.nan, right=np.nan)
    return out


def _channels(sampled: dict[str, np.ndarray]) -> dict[str, list]:
    return {name: _list(sampled[name] * scale, 2) for name, scale in TRACE_CHANNELS.items() if name in sampled}


def _list(values, places: int) -> list:
    """JSON-safe: rounded floats, NaN as null."""
    return [None if not np.isfinite(v) else round(float(v), places) for v in np.asarray(values, dtype=float)]


def _clean(obj):
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj
