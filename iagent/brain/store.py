"""Lap storage. Callers use the `LapStore` protocol; Parquet+SQLite is one implementation."""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import pandas as pd

from iagent.brain.laps import Lap
from iagent.brain.resample import resample_to_distance


@dataclass(frozen=True)
class LapRecord:
    lap_id: str
    session_id: str
    track: str
    car: str
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
        """`within_best` keeps only representative laps: valid and within that fraction (e.g.
        0.05) of the best valid lap among the *other* filter matches."""
        ...

    def load(self, lap_id: str, grid: bool = True) -> pd.DataFrame:
        """`grid=True` gives the distance-resampled lap, otherwise the raw 60 Hz samples."""
        ...


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


class ParquetLapStore:
    """Raw and distance-gridded laps as Parquet files, indexed in SQLite.

    Layout: `<root>/laps/<track>/<car>/<lap_id>.parquet` (raw) and `.grid.parquet`, plus
    `<root>/index.sqlite`. Files are never read by the index, so the index can be rebuilt.
    """

    def __init__(self, root: Path | str, grid_step_m: float = 1.0):
        self._root = Path(root)
        self._grid_step = grid_step_m
        self._root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._root / "index.sqlite")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS laps (
                lap_id TEXT PRIMARY KEY, session_id TEXT, track TEXT, car TEXT, seq INTEGER,
                sim_lap INTEGER, lap_time REAL, complete INTEGER, valid INTEGER,
                reasons TEXT, source TEXT, path TEXT, created_at TEXT, off_track_s REAL)"""
        )
        self._db.commit()

    def save(self, lap: Lap, source: str = "unknown") -> LapRecord:
        base = self._root / "laps" / _slug(lap.session.track_name) / _slug(lap.session.car_name)
        base.mkdir(parents=True, exist_ok=True)
        raw_path = base / f"{lap.lap_id}.parquet"
        lap.frames.to_parquet(raw_path, index=False)
        resample_to_distance(lap, self._grid_step).to_parquet(
            base / f"{lap.lap_id}.grid.parquet", index=False
        )

        record = LapRecord(
            lap_id=lap.lap_id,
            session_id=lap.session.session_id,
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
            "INSERT OR REPLACE INTO laps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.lap_id, record.session_id, record.track, record.car, record.seq,
                record.sim_lap, record.lap_time, int(record.complete), int(record.valid),
                ",".join(record.reasons), source, str(raw_path.relative_to(self._root)),
                datetime.now(timezone.utc).isoformat(), record.off_track_s,
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
        for column, value in (("track", track), ("car", car), ("session_id", session_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if valid_only:
            where.append("valid = 1")
        query = "SELECT lap_id, session_id, track, car, seq, sim_lap, lap_time, complete, valid, reasons, source, off_track_s FROM laps"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY session_id, seq"
        records = [
            LapRecord(
                lap_id=r[0], session_id=r[1], track=r[2], car=r[3], seq=r[4], sim_lap=r[5],
                lap_time=r[6], complete=bool(r[7]), valid=bool(r[8]),
                reasons=tuple(x for x in r[9].split(",") if x), source=r[10],
                off_track_s=r[11],
            )
            for r in self._db.execute(query, args)
        ]
        if within_best is not None:
            from iagent.brain.pace import representative  # avoid an import cycle

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
