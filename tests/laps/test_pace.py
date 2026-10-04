import pytest

from iagent.laps.pace import best_time, representative
from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.testing.synthetic import LapKind, SLOW_FACTOR, SyntheticSource


@pytest.fixture
def records(tmp_path):
    source = SyntheticSource(
        n_laps=7,
        kinds=[LapKind.CLEAN, LapKind.CLEAN, LapKind.OFF_TRACK, LapKind.SLOW,
               LapKind.PIT_IN, LapKind.RESET, LapKind.CLEAN],
    )
    store = ParquetLapStore(tmp_path)
    yield record(source, store, "synthetic")
    store.close()


def test_best_time_ignores_invalid_laps(records):
    valid_times = [r.lap_time for r in records if r.valid]
    assert best_time(records) == min(valid_times)
    assert all(r.valid for r in representative(records))


def test_close_laps_are_kept_including_an_off_track_one(records):
    seqs = [r.seq for r in representative(records, within=0.05)]
    assert seqs == [1, 2, 6]  # the off-track lap (2) cost no time, so it stays in


def test_slow_and_invalid_laps_are_excluded(records):
    kept = {r.seq for r in representative(records, within=0.05)}
    assert 3 not in kept  # driven 15% slower
    assert not kept & {0, 4, 5}  # partial, pit, reset


def test_tolerance_is_respected(records):
    loose = {r.seq for r in representative(records, within=SLOW_FACTOR - 1 + 0.01)}
    assert 3 in loose
    assert representative(records, within=0.0)[0].lap_time == best_time(records)


def test_no_valid_laps(tmp_path):
    source = SyntheticSource(n_laps=2, kinds=[LapKind.CLEAN, LapKind.PIT_IN])
    store = ParquetLapStore(tmp_path)
    recs = [r for r in record(source, store, "x") if not r.valid]
    assert representative(recs) == [] and best_time(recs) is None
    store.close()


def test_store_filter_matches_the_pure_function(tmp_path):
    source = SyntheticSource(n_laps=5, kinds=[LapKind.CLEAN, LapKind.CLEAN, LapKind.SLOW,
                                               LapKind.CLEAN, LapKind.CLEAN])
    store = ParquetLapStore(tmp_path)
    recs = record(source, store, "x")
    assert store.list(within_best=0.05) == representative(recs, 0.05)
    store.close()


def test_pace_is_judged_per_track_and_car(records):
    from dataclasses import replace

    from iagent.laps.pace import best_times

    # The same laps on a second, much faster "track": its best must not exclude the first group.
    other = [replace(r, track_key="elsewhere", lap_id=r.lap_id + "x", lap_time=r.lap_time / 2)
             for r in records if r.lap_time is not None]
    both = list(records) + other
    assert len(best_times(both)) == 2
    assert [r.seq for r in representative(both) if r.track_key != "elsewhere"] == [1, 2, 6]
    with pytest.raises(ValueError, match="several"):
        best_time(both)
