"""Lap storage. Callers use the `LapStore` protocol; Parquet+SQLite is one implementation."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import pandas as pd

from iagent.laps.resample import resample_to_distance
from iagent.laps.segment import Lap

SCHEMA_VERSION = 2
_COLUMNS = (
    "lap_id", "session_id", "track_key", "car_key", "track", "car", "seq", "sim_lap", "lap_time",
    "complete", "valid", "reasons", "source", "off_track_s", "path", "created_at",
)


@dataclass(frozen=True)
class LapRecord:
    lap_id: str
    session_id: str
    track_key: str  # grouping key, e.g. "spa-2024-up" (see SessionInfo.track_key)
    car_key: str  # grouping key, e.g. "formulair04"
    track: str  # display name
    car: str  # display name
    seq: int
    sim_lap: int | None
    lap_time: float | None
    complete: bool
    valid: bool
    reasons: tuple[str, ...]
    source: str
    off_track_s: float = 0.0


class LapStore(Protocol):
    def save(self, lap: Lap, source: str = "unknown") -> LapRecord: ...

    def list(
        self,
        track: str | None = None,
        car: str | None = None,
        session_id: str | None = None,
        valid_only: bool = False,
        within_best: float | None = None,
    ) -> list[LapRecord]:
        """`track`/`car` match the grouping keys. `within_best` keeps only representative laps:
        valid and within that fraction (e.g. 0.05) of the best valid lap for the same track and
        car among the other filter matches."""
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
        if has_table and version != SCHEMA_VERSION:
            self._db.close()
            raise RuntimeError(
                f"{self._root} holds a lap store in an older format (v{version}, need "
                f"v{SCHEMA_VERSION}). Delete it and re-ingest the recordings."
            )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS laps (
                lap_id TEXT PRIMARY KEY, session_id TEXT, track_key TEXT, car_key TEXT,
                track TEXT, car TEXT, seq INTEGER, sim_lap INTEGER, lap_time REAL,
                complete INTEGER, valid INTEGER, reasons TEXT, source TEXT, off_track_s REAL,
                path TEXT, created_at TEXT)"""
        )
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._db.commit()

    def save(self, lap: Lap, source: str = "unknown") -> LapRecord:
        base = self._root / "laps" / lap.session.track_key / lap.session.car_key
        base.mkdir(parents=True, exist_ok=True)
        raw_path = base / f"{lap.lap_id}.parquet"
        lap.frames.to_parquet(raw_path, index=False)
        resample_to_distance(lap, self._grid_step).to_parquet(
            base / f"{lap.lap_id}.grid.parquet", index=False
        )

        record = LapRecord(
            lap_id=lap.lap_id,
            session_id=lap.session.session_id,
            track_key=lap.session.track_key,
            car_key=lap.session.car_key,
            track=lap.session.track_name,
            car=lap.session.car_name,
            seq=lap.seq,
            sim_lap=lap.sim_lap,
            lap_time=lap.lap_time,
            complete=lap.complete,
            valid=lap.valid,
            reasons=tuple(lap.reasons),
            source=source,
            off_track_s=lap.off_track_s,
        )
        self._db.execute(
            f"INSERT OR REPLACE INTO laps ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))})",
            (
                record.lap_id, record.session_id, record.track_key, record.car_key, record.track,
                record.car, record.seq, record.sim_lap, record.lap_time, int(record.complete),
                int(record.valid), ",".join(record.reasons), source, record.off_track_s,
                str(raw_path.relative_to(self._root)), datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._db.commit()
        return record

    def list(
        self,
        track: str | None = None,
        car: str | None = None,
        session_id: str | None = None,
        valid_only: bool = False,
        within_best: float | None = None,
    ) -> list[LapRecord]:
        where, args = [], []
        for column, value in (("track_key", track), ("car_key", car), ("session_id", session_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if valid_only:
            where.append("valid = 1")
        query = f"SELECT {', '.join(_COLUMNS[:14])} FROM laps"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY track_key, car_key, session_id, seq"
        records = [
            LapRecord(
                lap_id=r[0], session_id=r[1], track_key=r[2], car_key=r[3], track=r[4], car=r[5],
                seq=r[6], sim_lap=r[7], lap_time=r[8], complete=bool(r[9]), valid=bool(r[10]),
                reasons=tuple(x for x in r[11].split(",") if x), source=r[12], off_track_s=r[13],
            )
            for r in self._db.execute(query, args)
        ]
        if within_best is not None:
            from iagent.laps.pace import representative  # pace imports LapRecord from here

            records = representative(records, within_best)
        return records

    def load(self, lap_id: str, grid: bool = True) -> pd.DataFrame:
        row = self._db.execute("SELECT path FROM laps WHERE lap_id = ?", (lap_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown lap {lap_id!r}")
        path = self._root / row[0]
        if grid:
            path = path.with_name(path.name.replace(".parquet", ".grid.parquet"))
        return pd.read_parquet(path)

    def close(self) -> None:
        self._db.close()
