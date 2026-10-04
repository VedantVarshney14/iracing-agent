import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore


@pytest.fixture
def store(tmp_path):
    s = ParquetLapStore(tmp_path)
    yield s
    s.close()


def test_record_saves_every_lap_with_flags(store, messy_source):
    records = record(messy_source, store, "synthetic")
    assert len(records) == 6
    assert [r.valid for r in store.list()] == [False, True, False, False, False, True]


def test_list_filters(store, messy_source):
    record(messy_source, store, "synthetic")
    assert len(store.list(valid_only=True)) == 2
    assert len(store.list(track="synthetic", car="synthcar")) == 6
    assert store.list(track="spa-2024-up") == []
    assert len(store.list(session_id="synthetic-2")) == 6


def test_load_raw_and_grid(store, clean_source):
    (first, *_) = record(clean_source, store, "synthetic")
    raw = store.load(first.lap_id, grid=False)
    grid = store.load(first.lap_id, grid=True)
    assert len(grid) == 3000
    assert len(raw) > 3000  # 60 Hz over ~58 s
    assert "lap_time_s" in raw and "lap_time_s" in grid


def test_saved_record_round_trips(store, clean_source):
    saved = record(clean_source, store, "synthetic")
    assert store.list() == saved


def test_resaving_replaces_rather_than_duplicates(store, clean_source):
    record(clean_source, store, "synthetic")
    record(clean_source, store, "synthetic")
    assert len(store.list()) == 4


def test_unknown_lap(store):
    with pytest.raises(KeyError):
        store.load("nope")


def test_persists_across_instances(tmp_path, clean_source):
    first = ParquetLapStore(tmp_path)
    record(clean_source, first, "synthetic")
    first.close()
    second = ParquetLapStore(tmp_path)
    assert len(second.list()) == 4
    second.close()


def test_old_format_store_is_refused(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "index.sqlite")
    db.execute("CREATE TABLE laps (lap_id TEXT PRIMARY KEY)")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="older format"):
        ParquetLapStore(tmp_path)


def test_files_are_laid_out_by_track_and_car_key(store, clean_source, tmp_path):
    record(clean_source, store, "synthetic")
    assert len(list((tmp_path / "laps" / "synthetic" / "synthcar").glob("*.grid.parquet"))) == 4
