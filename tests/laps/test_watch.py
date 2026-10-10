import os

import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.laps.watch import TelemetryWatcher
from iagent.telemetry.ibt import IbtSource
from iagent.testing.ibt_writer import write_ibt
from iagent.testing.synthetic import SyntheticSource

NAME = "synthcar_synthetic 2026-10-04 15-00-00.ibt"


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "telemetry"
    path.mkdir()
    return path


def recording(folder, name=NAME, laps=3):
    source = SyntheticSource(n_laps=laps, seed=4)
    path = write_ibt(folder / name, source.session, source.frames())
    os.utime(path, (1000.0, 1000.0))  # finished long ago, as far as the watcher's clock goes
    return path


def lap_count(root) -> int:
    store = ParquetLapStore(root)
    try:
        return len(store.list())
    finally:
        store.close()


def test_a_finished_recording_is_ingested_once(tmp_path, folder):
    recording(folder)
    ws = tmp_path / "ws"
    watcher = TelemetryWatcher(ws, folder)
    assert watcher.scan(now=2000) == []  # first sight: wait for the size to hold still
    assert watcher.scan(now=2010) == [NAME]
    assert lap_count(ws) == 3
    status = watcher.status()
    assert status["found"] and status["files_ingested"] == 1 and status["version"] == 1
    assert status["last"]["file"] == NAME and status["last"]["laps"] == 3
    assert (ws / "tracks" / "synthetic" / "track.json").exists()

    assert watcher.scan(now=2020) == []
    again = TelemetryWatcher(ws, folder)  # restart: remembers what it ingested
    again.scan(now=3000)
    assert again.scan(now=3010) == [] and lap_count(ws) == 3


def test_a_recording_in_progress_is_left_alone(tmp_path, folder):
    path = recording(folder)
    watcher = TelemetryWatcher(tmp_path / "ws", folder, settle_s=15)
    watcher.scan(now=2000)
    with path.open("ab") as f:  # iRacing still writing
        f.write(b"\0" * 64)
    os.utime(path, (1000.0, 1000.0))
    assert watcher.scan(now=2010) == []
    os.utime(path, (2005.0, 2005.0))  # size settled but written 5 s ago
    assert watcher.scan(now=2010) == []


def test_sessions_ingested_by_hand_are_skipped(tmp_path, folder):
    path = recording(folder)
    ws = tmp_path / "ws"
    store = ParquetLapStore(ws)
    record(IbtSource(path), store, "by hand")
    store.close()
    watcher = TelemetryWatcher(ws, folder)
    watcher.scan(now=2000)
    assert watcher.scan(now=2010) == [] and watcher.status()["version"] == 0
    assert lap_count(ws) == 3


def test_a_broken_file_is_reported_not_retried(tmp_path, folder):
    bad = folder / "broken 2026-10-04 16-00-00.ibt"
    bad.write_bytes(b"not an ibt")
    os.utime(bad, (1000.0, 1000.0))
    watcher = TelemetryWatcher(tmp_path / "ws", folder)
    watcher.scan(now=2000)
    assert watcher.scan(now=2010) == []
    assert "broken" in watcher.status()["error"]
    assert watcher.scan(now=2020) == []


def test_missing_folder(tmp_path):
    watcher = TelemetryWatcher(tmp_path / "ws", tmp_path / "nope")
    assert watcher.scan() == [] and watcher.status()["found"] is False


def test_the_folder_can_be_changed_and_is_remembered(tmp_path, folder):
    from iagent.laps.watch import saved_telemetry_dir

    ws = tmp_path / "ws"
    assert saved_telemetry_dir(ws) is None
    watcher = TelemetryWatcher(ws, tmp_path / "elsewhere")
    recording(folder)
    watcher.set_folder(folder)
    assert saved_telemetry_dir(ws) == folder and watcher.status()["folder"] == str(folder)
    watcher.scan(now=2000)
    assert watcher.scan(now=2010) == [NAME]
