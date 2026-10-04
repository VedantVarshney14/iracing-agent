import numpy as np
import pytest

from iagent.analysis.corners import (
    MS_TO_KPH,
    CornerMap,
    carry_names,
    compare_corners,
    consistency,
    corner_metrics,
    derive_corner_map,
    load_map,
    save_map,
)
from iagent.laps.pace import representative
from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.testing.synthetic import DEFAULT_TRACK, PICKUP_M, SyntheticCorner, SyntheticSource, SyntheticTrack


def _laps(tmp_path, track=DEFAULT_TRACK, n_laps=5, **kw):
    source = SyntheticSource(track=track, n_laps=n_laps, seed=3, **kw)
    store = ParquetLapStore(tmp_path)
    recs = representative(record(source, store, "synthetic"))
    grids = [store.load(r.lap_id) for r in recs]
    store.close()
    return recs, grids, source.truth


@pytest.fixture
def laps(tmp_path):
    return _laps(tmp_path)


@pytest.fixture
def cmap(laps):
    recs, grids, _ = laps
    return derive_corner_map(grids, "synthetic", "synthcar", [r.lap_id for r in recs])


def test_finds_every_corner_with_direction_and_apex(cmap):
    assert len(cmap.corners) == len(DEFAULT_TRACK.corners)
    for derived, truth in zip(cmap.corners, DEFAULT_TRACK.corners):
        assert derived.direction == ("R" if truth.direction > 0 else "L")
        assert derived.apex_m == pytest.approx(truth.apex_m, abs=5)
        assert derived.entry_m < derived.apex_m < derived.exit_m
        assert not derived.flat


def test_segments_tile_the_lap(cmap):
    segs = [(c.segment_start_m, c.segment_end_m) for c in cmap.corners]
    assert segs[0][0] == 0 and segs[-1][1] == pytest.approx(DEFAULT_TRACK.length_m)
    assert all(a[1] == b[0] for a, b in zip(segs, segs[1:]))
    # Each segment ends before the next corner's braking starts (at the speed peak).
    for c, nxt in zip(cmap.corners, DEFAULT_TRACK.corners[1:]):
        assert c.exit_m < c.segment_end_m <= nxt.brake_m + 1


def test_metrics_match_ground_truth(laps, cmap):
    recs, grids, truth = laps
    for rec, grid in zip(recs, grids):
        rows = corner_metrics(grid, rec.lap_time, cmap)
        lap_truth = truth[rec.seq]
        assert sum(r["time_s"] for r in rows) == pytest.approx(rec.lap_time, abs=0.005)
        for row, corner in zip(rows, DEFAULT_TRACK.corners):
            assert row["brake_m"] == pytest.approx(lap_truth.brake_m[corner.name], abs=2)
            assert row["min_speed_kph"] == pytest.approx(lap_truth.min_speed[corner.name] * MS_TO_KPH, abs=1.5)
            assert row["min_speed_m"] == pytest.approx(corner.apex_m, abs=3)
            assert row["full_throttle_m"] == pytest.approx(corner.apex_m + PICKUP_M, abs=3)


def test_compare_reports_brake_and_speed_differences(laps, cmap):
    recs, grids, truth = laps
    (a, ga), (b, gb) = (recs[0], grids[0]), (recs[1], grids[1])
    rows = compare_corners(corner_metrics(ga, a.lap_time, cmap), corner_metrics(gb, b.lap_time, cmap))
    assert sum(r["delta_s"] for r in rows) == pytest.approx(a.lap_time - b.lap_time, abs=0.01)
    for row, corner in zip(rows, DEFAULT_TRACK.corners):
        expected = truth[a.seq].brake_m[corner.name] - truth[b.seq].brake_m[corner.name]
        assert row["brake_diff_m"] == pytest.approx(expected, abs=2)  # > 0 means braked later


def test_consistency_reflects_brake_jitter(tmp_path):
    steady = consistency_of(tmp_path / "steady", brake_jitter_m=0.5)
    messy = consistency_of(tmp_path / "messy", brake_jitter_m=12.0)
    assert max(r["brake_spread_m"] for r in steady) <= 2
    assert np.mean([r["brake_spread_m"] for r in messy]) > 5


def consistency_of(path, **kw):
    recs, grids, _ = _laps(path, n_laps=8, **kw)
    cmap = derive_corner_map(grids, "synthetic", "synthcar", [r.lap_id for r in recs])
    return consistency([corner_metrics(g, r.lap_time, cmap) for r, g in zip(recs, grids)])


def test_chicane_splits_and_double_apex_merges(tmp_path):
    # The generator brakes from top speed into every corner, so braking zones need >= ~100 m.
    track = SyntheticTrack("Shapes", 3000.0, (
        SyntheticCorner("chicane-R", apex_m=600, brake_m=480, min_speed=25.0, direction=1),
        SyntheticCorner("chicane-L", apex_m=720, brake_m=600, min_speed=25.0, direction=-1),
        SyntheticCorner("double-1", apex_m=1600, brake_m=1480, min_speed=30.0, direction=1),
        SyntheticCorner("double-2", apex_m=1700, brake_m=1580, min_speed=30.0, direction=1),
        SyntheticCorner("flat", apex_m=2400, brake_m=2380, min_speed=58.0, direction=-1),
    ))
    recs, grids, _ = _laps(tmp_path, track=track, n_laps=4)
    cmap = derive_corner_map(grids, "shapes", "synthcar", [r.lap_id for r in recs])
    assert [c.direction for c in cmap.corners] == ["R", "L", "R", "L"]
    chicane_r, chicane_l, double, flat = cmap.corners
    assert chicane_r.exit_m < chicane_l.entry_m
    assert double.entry_m < 1600 and double.exit_m > 1700  # one corner, both apexes
    assert flat.flat and flat.apex_m == pytest.approx(2400, abs=10)


def test_map_round_trips_and_names_survive_remapping(tmp_path, cmap):
    cmap.corners[0].name, cmap.corners[0].name_source, cmap.corners[0].name_confidence = "Turn One", "driver", "high"
    save_map(tmp_path, cmap)
    loaded = load_map(tmp_path, "synthetic")
    assert loaded == cmap
    fresh = CornerMap.from_json(cmap.to_json())
    fresh.corners[0].name = None
    assert carry_names(loaded, fresh).corners[0].name == "Turn One"
    assert load_map(tmp_path, "nowhere") is None


def test_needs_lateral_g(laps):
    recs, grids, _ = laps
    with pytest.raises(ValueError, match="LatAccel"):
        derive_corner_map([g.drop(columns="LatAccel") for g in grids], "x", "y", [])


def test_off_track_is_located_in_its_corner(tmp_path):
    from iagent.testing.synthetic import LapKind

    # OFF_TRACK laps leave the track at 1500-1560 m: in T2's segment (T2 exits ~1285 m, T3 brakes at 1770 m).
    recs, grids, _ = _laps(tmp_path, n_laps=4, kinds=[LapKind.CLEAN, LapKind.CLEAN, LapKind.OFF_TRACK, LapKind.CLEAN])
    cmap = derive_corner_map(grids, "synthetic", "synthcar", [r.lap_id for r in recs])
    by_seq = {r.seq: corner_metrics(g, r.lap_time, cmap) for r, g in zip(recs, grids)}
    assert [row["off_track_m"] for row in by_seq[2]] == [0, 60, 0, 0]
    assert all(row["off_track_m"] == 0 for row in by_seq[1])
    rows = compare_corners(by_seq[1], by_seq[2])
    assert rows[1]["ref_off_track_m"] == 60 and rows[1]["off_track_m"] == 0
