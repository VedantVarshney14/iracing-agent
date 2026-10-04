import numpy as np
import pytest

from iagent.laps.resample import resample_to_distance
from tests.laps.test_segment import segment


def complete_laps(source):
    return [lap for lap in segment(source) if lap.complete]


def first_brake_point(grid, threshold=0.05, after=0.0):
    hit = grid[(grid["Brake"] > threshold) & (grid["LapDist"] > after)]
    return hit["LapDist"].iloc[0]


def test_grid_shape_and_monotonic_distance(clean_source):
    lap = complete_laps(clean_source)[0]
    grid = resample_to_distance(lap, step_m=1.0)
    assert len(grid) == 3000
    assert grid["LapDist"].is_monotonic_increasing
    assert grid["LapDist"].iloc[0] == 0.0
    assert not grid.isna().any().any()


def test_brake_points_recovered_within_a_metre(clean_source):
    """Resampling must not smear the feature the whole product depends on."""
    for lap in complete_laps(clean_source):
        truth = clean_source.truth[lap.seq]
        grid = resample_to_distance(lap, step_m=0.5)
        for corner, brake_m in truth.brake_m.items():
            found = first_brake_point(grid, after=brake_m - 40)
            assert found == pytest.approx(brake_m, abs=1.0), corner


def test_elapsed_time_reaches_lap_time(clean_source):
    lap = complete_laps(clean_source)[0]
    grid = resample_to_distance(lap)
    assert grid["lap_time_s"].iloc[0] == pytest.approx(0.0, abs=0.05)
    assert grid["lap_time_s"].iloc[-1] == pytest.approx(lap.lap_time, abs=0.1)
    assert grid["lap_time_s"].is_monotonic_increasing


def test_min_speed_per_corner_matches_truth(clean_source):
    lap = complete_laps(clean_source)[0]
    truth = clean_source.truth[lap.seq]
    grid = resample_to_distance(lap)
    for corner, apex in (("T1", 600), ("T2", 1200), ("T3", 1900), ("T4", 2500)):
        window = grid[(grid["LapDist"] > apex - 30) & (grid["LapDist"] < apex + 30)]
        assert window["Speed"].min() == pytest.approx(truth.min_speed[corner], abs=0.3)


def test_discrete_channels_are_never_interpolated(clean_source):
    grid = resample_to_distance(complete_laps(clean_source)[0])
    assert set(np.unique(grid["Gear"])) <= {1, 2, 3, 4, 5, 6}
    assert set(np.unique(grid["OnPitRoad"])) <= {0.0, 1.0}


def test_incomplete_lap_is_nan_outside_the_driven_range(messy_source):
    partial = segment(messy_source)[0]  # starts 1200 m in
    grid = resample_to_distance(partial)
    assert grid.loc[grid["LapDist"] < 1190, "Speed"].isna().all()
    assert grid.loc[grid["LapDist"] > 1300, "Speed"].notna().all()
