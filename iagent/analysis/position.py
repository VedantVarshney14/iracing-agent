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
