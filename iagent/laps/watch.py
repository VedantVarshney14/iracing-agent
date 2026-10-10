"""Watch iRacing's telemetry folder and ingest recordings once they're finished.

iRacing writes a `.ibt` while you drive (recording on, Alt+L) and leaves it alone afterwards. A
file is ingested when its size hasn't changed since the previous scan and it hasn't been written
for `settle_s`, so a session in progress is left until it ends. What has been ingested is
remembered in the workspace (`cache/telemetry-ingested.json`); sessions already ingested by hand
(`iagent ingest`, or a file dropped into the UI) are recognised and skipped.

The folder can be changed from the UI (e.g. to a OneDrive or Dropbox copy of the sim PC's folder,
on a Mac); the choice is saved in the workspace's `settings.json`.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.laps.tracks import update_track_info
from iagent.telemetry.ibt import IbtSource, session_id_from_filename

logger = logging.getLogger("iagent.watch")

# One ingest at a time, whether from the watcher, a rescan or an upload: they share the lap store.
_INGEST_LOCK = threading.Lock()


def settings_path(workspace: Path) -> Path:
    return workspace / "settings.json"


def saved_telemetry_dir(workspace: Path) -> Path | None:
    """The telemetry folder chosen in the UI, if any."""
    path = settings_path(workspace)
    folder = json.loads(path.read_text()).get("telemetry_dir") if path.exists() else None
    return Path(folder) if folder else None


def save_telemetry_dir(workspace: Path, folder: Path) -> None:
    path = settings_path(workspace)
    settings = json.loads(path.read_text()) if path.exists() else {}
    settings["telemetry_dir"] = str(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=1))


def ingest_file(workspace: Path, path: Path) -> dict:
    """Ingest one recording into the workspace, unless its session is already there.
    Returns {"laps", "valid", "track", "track_key", "car_key"} (or {"laps": 0, "skipped": ...})."""
    with _INGEST_LOCK:
        store = ParquetLapStore(workspace)
        try:
            session_id = session_id_from_filename(path)
            if store.list(session_id=session_id):
                return {"laps": 0, "skipped": "already ingested"}
            source = IbtSource(path)
            records = record(source, store, source_label=path.name)
            update_track_info(workspace, source.session)
        finally:
            store.close()
    logger.info("Ingested %s: %d laps", path.name, len(records))
    first = records[0] if records else None
    return {"laps": len(records), "valid": sum(r.valid for r in records),
            "track": first.track if first else None,
            "track_key": first.track_key if first else None, "car_key": first.car_key if first else None}


def default_telemetry_dir() -> Path:
    """`IAGENT_TELEMETRY_DIR`, else iRacing's own folder: Documents/iRacing/telemetry (checking a
    OneDrive-redirected Documents folder too, common on Windows)."""
    if env := os.environ.get("IAGENT_TELEMETRY_DIR"):
        return Path(env)
    home = Path.home()
    for docs in (home / "Documents", home / "OneDrive" / "Documents"):
        if (docs / "iRacing").exists():
            return docs / "iRacing" / "telemetry"
    return home / "Documents" / "iRacing" / "telemetry"


class TelemetryWatcher:
    def __init__(self, workspace: Path, folder: Path, interval_s: float = 10.0, settle_s: float = 15.0):
        self.workspace = workspace
        self.folder = folder
        self.interval_s = interval_s
        self.settle_s = settle_s
        self._state_path = workspace / "cache" / "telemetry-ingested.json"
        self._done: dict[str, dict] = json.loads(self._state_path.read_text()) if self._state_path.exists() else {}
        self._sizes: dict[str, int] = {}  # size at the previous scan, to tell a finished file
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()  # the background thread and a rescan from the UI
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.version = 0  # bumped whenever new laps arrive, so the UI knows to reload
        self.last: dict | None = None
        self.error: str | None = None

    def status(self) -> dict:
        with self._lock:
            return {
                "folder": str(self.folder),
                "found": self.folder.is_dir(),
                "watching": self._thread is not None and self._thread.is_alive(),
                "files_ingested": sum(1 for d in self._done.values() if not d.get("error")),
                "last": self.last,
                "error": self.error,
                "version": self.version,
            }

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="telemetry-watch", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_folder(self, folder: Path) -> None:
        """Watch another folder from now on (and remember it for next time)."""
        with self._scan_lock, self._lock:
            self.folder = folder
            self._sizes = {}
            self.error = None
        save_telemetry_dir(self.workspace, folder)

    def rescan(self, pause_s: float = 2.0) -> list[str]:
        """Look now rather than at the next interval: two scans a moment apart, so files whose
        size holds still are ingested straight away."""
        found = self.scan()
        time.sleep(pause_s)
        return found + self.scan()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception as e:  # keep watching; report the problem
                logger.exception("Telemetry scan failed")
                with self._lock:
                    self.error = str(e)
            self._stop.wait(self.interval_s)

    def scan(self, now: float | None = None) -> list[str]:
        """One pass over the folder: ingest finished recordings. Returns the files ingested."""
        with self._scan_lock:
            return self._scan(now)

    def _scan(self, now: float | None) -> list[str]:
        if not self.folder.is_dir():
            return []
        now = time.time() if now is None else now
        ingested = []
        for path in sorted(self.folder.glob("*.ibt")):
            stat = path.stat()
            key = path.name
            done = self._done.get(key)
            if done and done["size"] == stat.st_size:
                continue
            previous = self._sizes.get(key)
            self._sizes[key] = stat.st_size
            if previous != stat.st_size or now - stat.st_mtime < self.settle_s:
                continue  # still being written (or first sight of it): look again next scan
            entry = {"size": stat.st_size, "at": datetime.now(timezone.utc).isoformat()}
            try:
                entry.update(ingest_file(self.workspace, path))
                if entry.get("laps"):
                    ingested.append(key)
            except Exception as e:
                logger.warning("Couldn't ingest %s: %s", key, e)
                entry["error"] = str(e)
            with self._lock:
                self._done[key] = entry
                if entry.get("laps") or entry.get("error"):
                    self.last = {"file": key, **entry}
                if entry.get("laps"):
                    self.version += 1
                    self.error = None
                elif entry.get("error"):
                    self.error = f"{key}: {entry['error']}"
            self._save()
        return ingested

    def _save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._state_path.write_text(json.dumps(self._done, indent=1))
