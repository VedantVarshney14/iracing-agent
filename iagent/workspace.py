"""The coach's workspace: the driver's lap store, reference laps and per-track files.

Shared by the CLI and the web UI. Errors are `WorkspaceError`s with a message meant for the
driver (or the agent), which each front end reports in its own way.
"""

import json
import logging
from pathlib import Path

import pandas as pd

from iagent.analysis.corners import CornerMap, derive_corner_map, load_map, save_map
from iagent.laps.pace import group_of, representative
from iagent.laps.store import LapRecord, ParquetLapStore

logger = logging.getLogger("iagent.workspace")


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root: Path):
        self.root = root
        self._store: ParquetLapStore | None = None
        self._refs: ParquetLapStore | None = None

    def store(self, create: bool = False) -> ParquetLapStore:
        if self._store is None:
            if not create and not (self.root / "index.sqlite").exists():
                raise WorkspaceError(
                    f"No lap store in {self.root}. Run `iagent ingest <file.ibt>` first, or "
                    "set --workspace / IAGENT_WORKSPACE."
                )
            try:
                self._store = ParquetLapStore(self.root)
            except RuntimeError as e:
                raise WorkspaceError(str(e)) from e
        return self._store

    def refs(self, create: bool = False) -> ParquetLapStore | None:
        """Reference laps (other drivers', e.g. from Garage61), kept apart from the driver's own
        laps so they never count towards "your best" or the pace filter."""
        root = self.root / "reference"
        if self._refs is None and (create or (root / "index.sqlite").exists()):
            self._refs = ParquetLapStore(root)
        return self._refs

    def close(self) -> None:
        for store in (self._store, self._refs):
            if store is not None:
                store.close()

    def find(self, lap_id: str) -> LapRecord:
        """A lap by id: the driver's own laps first, then reference laps."""
        for store in (self.store(), self.refs()):
            if store is not None:
                matches = [r for r in store.list() if r.lap_id == lap_id]
                if matches:
                    return matches[0]
        raise WorkspaceError(f"Unknown lap {lap_id!r}. See `iagent laps list` or `iagent refs list`.")

    def load(self, lap_id: str) -> pd.DataFrame:
        """The distance-gridded lap, from whichever store holds it."""
        try:
            return self.store().load(lap_id)
        except KeyError:
            refs = self.refs()
            if refs is None:
                raise
            return refs.load(lap_id)

    def reference(self, rec: LapRecord, ref_id: str | None) -> LapRecord:
        """REF_ID's record, or the fastest other valid lap on the same track and car."""
        if ref_id is None:
            candidates = [r for r in self.store().list(track=rec.track_key, car=rec.car_key, valid_only=True)
                          if r.lap_id != rec.lap_id and r.lap_time is not None]
            if not candidates:
                raise WorkspaceError("No other valid lap on this track and car to compare against.")
            return min(candidates, key=lambda r: r.lap_time)
        ref = self.find(ref_id)
        if group_of(ref) != group_of(rec):
            raise WorkspaceError(
                f"{ref_id} is {ref.track_key}/{ref.car_key}, {rec.lap_id} is {rec.track_key}/{rec.car_key}: "
                "laps on different tracks or cars can't be compared."
            )
        return ref

    def corner_map(self, track: str) -> CornerMap:
        """The saved map for a track, derived (and saved) on first use."""
        cmap = load_map(self.root, track)
        if cmap is None:
            cmap = self.derive_map(track, None)
            path = save_map(self.root, cmap)
            logger.info("No corner map for %s yet: derived %d corners into %s", track, len(cmap.corners), path)
        return cmap

    def derive_map(self, track: str, car: str | None) -> CornerMap:
        reps = representative(self.store().list(track=track, car=car))
        if not reps:
            raise WorkspaceError(f"No representative laps for track {track!r}; see `iagent tracks`.")
        if car is None:  # use the car with the most representative laps
            counts: dict[str, int] = {}
            for r in reps:
                counts[r.car_key] = counts.get(r.car_key, 0) + 1
            car = max(counts, key=counts.__getitem__)
            reps = [r for r in reps if r.car_key == car]
        grids = [self.store().load(r.lap_id) for r in reps]
        try:
            return derive_corner_map(grids, track, car, [r.lap_id for r in reps])
        except ValueError as e:
            raise WorkspaceError(str(e)) from e

    def meta_path(self, lap_id: str) -> Path:
        return self.root / "reference" / "meta" / f"{lap_id}.json"

    def ref_meta(self, lap_id: str) -> dict:
        """What's known about a reference lap beyond its telemetry (driver, date, conditions)."""
        path = self.meta_path(lap_id)
        return json.loads(path.read_text()) if path.exists() else {}
