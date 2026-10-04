"""Car position from iRacing's `Lat`/`Lon` channels (decimal degrees).

Recorded `.ibt` files and Garage61 exports carry them; the live SDK does not, so laps may have
no position and callers must check `has_position` first.
"""

import numpy as np
import pandas as pd

EARTH_RADIUS_M = 6_371_000.0


def has_position(df: pd.DataFrame) -> bool:
    """True when the lap has usable `Lat`/`Lon` (exports without position fill them with 0)."""
    return all(c in df and df[c].notna().any() and (df[c].fillna(0.0) != 0.0).any() for c in ("Lat", "Lon"))


def to_local_xy(
    lat: np.ndarray, lon: np.ndarray, origin: tuple[float, float] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Metres east (x) and north (y) of `origin` (lat, lon; defaults to the points' mean).

    An equirectangular projection about the origin: over a circuit a few km across the error is
    well under 0.1%, far below the line differences worth looking at.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    lat0, lon0 = origin if origin is not None else (float(np.nanmean(lat)), float(np.nanmean(lon)))
    x = np.radians(lon - lon0) * EARTH_RADIUS_M * np.cos(np.radians(lat0))
    y = np.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def from_local_xy(x: np.ndarray, y: np.ndarray, origin: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of `to_local_xy` for a given origin: (lat, lon) in degrees."""
    lat0, lon0 = origin
    lat = lat0 + np.degrees(np.asarray(y, dtype=float) / EARTH_RADIUS_M)
    lon = lon0 + np.degrees(np.asarray(x, dtype=float) / (EARTH_RADIUS_M * np.cos(np.radians(lat0))))
    return lat, lon


def line_offset(
    x: np.ndarray, y: np.ndarray, ref_x: np.ndarray, ref_y: np.ndarray, search: int = 30
) -> tuple[np.ndarray, np.ndarray]:
    """How far, and to which side, a line runs from a reference line: for each point, the signed
    distance (m) to the nearest reference point, positive to the left of the reference's direction
    of travel, and that point's index.

    Both lines are sampled on the same distance grid (local metres, e.g. from `to_local_xy`); the
    nearest point is searched within `search` grid points either side, so a corner that doubles
    back on itself can't match the wrong part of the track. Missing points give NaN and index -1.
    """
    p = np.c_[np.asarray(x, float), np.asarray(y, float)]
    r = np.c_[np.asarray(ref_x, float), np.asarray(ref_y, float)]
    n = len(r)
    tangent = np.gradient(r, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)

    window = np.arange(-search, search + 1)
    cand = np.clip(np.arange(len(p))[:, None] + window[None, :], 0, n - 1)
    dist2 = ((r[cand] - p[:, None, :]) ** 2).sum(axis=2)
    dist2 = np.where(np.isnan(dist2), np.inf, dist2)
    nearest = cand[np.arange(len(p)), dist2.argmin(axis=1)]

    v = p - r[nearest]
    t = tangent[nearest]
    offset = t[:, 0] * v[:, 1] - t[:, 1] * v[:, 0]  # cross product: + to the left
    missing = ~np.isfinite(offset)
    nearest = np.where(missing, -1, nearest)
    return np.where(missing, np.nan, offset), nearest
