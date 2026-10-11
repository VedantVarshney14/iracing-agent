"""Lap storage. Callers use the `LapStore` protocol; Parquet+SQLite is one implementation."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import pandas as pd

from iagent.laps.lap import Lap
from iagent.laps.pace import representative
from iagent.laps.resample import resample_to_distance

SCHEMA_VERSION = 3  # v3 added track_length_m
_COLUMNS = (
    "lap_id", "session_id", "track_key", "car_key", "track", "car", "track_length_m", "seq", "sim_lap",
    "lap_time", "complete", "valid", "reasons", "source", "off_track_s", "path", "created_at",
)
_LAP_COLUMNS = _COLUMNS[:15]  # the ones that make a `Lap`


class LapStore(Protocol):
    """Where laps are kept: save a segmented lap, list laps (with filters), load a lap's samples.
    The format is the implementation's business."""

    def save(self, lap: Lap) -> Lap:
        """Stores a lap with its `frames`; returns it without them."""
        ...

    def list(
        self,
        track: str | None = None,
        car: str | None = None,
        session_id: str | None = None,
        valid_only: bool = False,
        within_best: float | None = None,
    ) -> list[Lap]:
        """Laps without their samples. `track`/`car` match the grouping keys. `within_best` keeps
        only representative laps: valid and within that fraction (e.g. 0.05) of the best valid lap
        for the same track and car among the other filter matches."""
        ...

    def load(self, lap_id: str, grid: bool = True) -> pd.DataFrame:
        """`grid=True` gives the distance-resampled lap, otherwise the raw 60 Hz samples."""
        ...


class ParquetLapStore:
    """Raw and distance-gridded laps as Parquet files, indexed in SQLite.

    Layout: `<root>/laps/<track_key>/<car_key>/<lap_id>.parquet` (raw) and `.grid.parquet`, plus
    `<root>/index.sqlite`.
    """

    def __init__(self, root: Path | str, grid_step_m: float = 1.0):
        self._root = Path(root)
        self._grid_step = grid_step_m
        self._root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._root / "index.sqlite")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        has_table = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'laps'"
        ).fetchone()
        if has_table and version == 2:
            self._db.execute("ALTER TABLE laps ADD COLUMN track_length_m REAL")
        elif has_table and version != SCHEMA_VERSION:
            self._db.close()
            raise RuntimeError(
                f"{self._root} holds a lap store in an older format (v{version}, need "
                f"v{SCHEMA_VERSION}). Delete it and re-ingest the recordings."
            )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS laps (
                lap_id TEXT PRIMARY KEY, session_id TEXT, track_key TEXT, car_key TEXT,
                track TEXT, car TEXT, track_length_m REAL, seq INTEGER, sim_lap INTEGER, lap_time REAL,
                complete INTEGER, valid INTEGER, reasons TEXT, source TEXT, off_track_s REAL,
                path TEXT, created_at TEXT)"""
        )
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._db.commit()

    def save(self, lap: Lap) -> Lap:
        """Write the lap's raw samples and its 1 m grid, and index it."""
        if lap.frames is None:
            raise ValueError(f"Lap {lap.lap_id} has no samples to save.")
        base = self._root / "laps" / lap.track_key / lap.car_key
        base.mkdir(parents=True, exist_ok=True)
        raw_path = base / f"{lap.lap_id}.parquet"
        lap.frames.to_parquet(raw_path, index=False)
        resample_to_distance(lap, self._grid_step).to_parquet(
            base / f"{lap.lap_id}.grid.parquet", index=False
        )
        self._db.execute(
            f"INSERT OR REPLACE INTO laps ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))})",
            (
                lap.lap_id, lap.session_id, lap.track_key, lap.car_key, lap.track, lap.car,
                lap.track_length_m, lap.seq, lap.sim_lap, lap.lap_time, int(lap.complete),
                int(lap.valid), ",".join(lap.reasons), lap.source, lap.off_track_s,
                str(raw_path.relative_to(self._root)), datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._db.commit()
        return lap.without_frames()

    def list(
        self,
        track: str | None = None,
        car: str | None = None,
        session_id: str | None = None,
        valid_only: bool = False,
        within_best: float | None = None,
    ) -> list[Lap]:
        """Laps from the index, filtered, in track, car, session and lap order."""
        where, args = [], []
        for column, value in (("track_key", track), ("car_key", car), ("session_id", session_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if valid_only:
            where.append("valid = 1")
        query = f"SELECT {', '.join(_LAP_COLUMNS)} FROM laps"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY track_key, car_key, session_id, seq"
        laps = [_lap(dict(zip(_LAP_COLUMNS, row))) for row in self._db.execute(query, args)]
        if within_best is not None:
            laps = representative(laps, within_best)
        return laps

    def load(self, lap_id: str, grid: bool = True) -> pd.DataFrame:
        """A lap's samples: on the 1 m grid, or raw. KeyError for an unknown lap."""
        row = self._db.execute("SELECT path FROM laps WHERE lap_id = ?", (lap_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown lap {lap_id!r}")
        path = self._root / row[0]
        if grid:
            path = path.with_name(path.name.replace(".parquet", ".grid.parquet"))
        return pd.read_parquet(path)

    def close(self) -> None:
        self._db.close()


def _lap(row: dict) -> Lap:
    """`complete` and `valid` are columns only to filter on; a `Lap` derives them from `reasons`."""
    del row["complete"], row["valid"]
    row["reasons"] = tuple(x for x in row["reasons"].split(",") if x)
    return Lap(**row)
