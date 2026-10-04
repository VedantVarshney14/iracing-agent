import numpy as np
import pandas as pd
import pytest

from iagent.analysis.position import from_local_xy, has_position, to_local_xy
from iagent.laps.resample import resample_to_distance
from iagent.testing.synthetic import CORNER_WIDTH_M, DEFAULT_TRACK, LINE_WIDENING_M, ORIGIN, build_profile
from tests.laps.test_segment import segment


def path_length(lat, lon) -> float:
    x, y = to_local_xy(lat, lon)
    return float(np.hypot(np.diff(x), np.diff(y)).sum())


def test_local_xy_round_trips_and_measures_metres():
    origin = (50.44, 5.97)
    x = np.array([0.0, 1000.0, -250.0])
    y = np.array([0.0, 0.0, 400.0])
    lat, lon = from_local_xy(x, y, origin)
    back_x, back_y = to_local_xy(lat, lon, origin)
    assert back_x == pytest.approx(x, abs=1e-6) and back_y == pytest.approx(y, abs=1e-6)
    assert lat[1] == pytest.approx(origin[0]) and lon[2] < origin[1]  # east is +x, west is -x


def test_has_position():
    assert has_position(pd.DataFrame({"Lat": [50.4, 50.5], "Lon": [5.9, 6.0]}))
    assert not has_position(pd.DataFrame({"Lat": [0.0, 0.0], "Lon": [0.0, 0.0]}))  # export without GPS
    assert not has_position(pd.DataFrame({"Speed": [1.0]}))  # live telemetry
    assert not has_position(pd.DataFrame({"Lat": [np.nan], "Lon": [np.nan]}))  # grid outside a partial lap


def test_synthetic_laps_trace_a_closed_loop_as_long_as_the_lap(clean_source):
    length = clean_source.session.track_length_m
    for lap in segment(clean_source):
        if not lap.complete:
            continue
        grid = resample_to_distance(lap)
        assert has_position(grid)
        assert path_length(grid["Lat"], grid["Lon"]) == pytest.approx(length, rel=0.002)
        x, y = to_local_xy(grid["Lat"], grid["Lon"])
        assert np.hypot(x[-1] - x[0], y[-1] - y[0]) < 2.0  # ends where it started


def test_synthetic_line_widens_with_apex_speed():
    track = DEFAULT_TRACK
    base = build_profile(track)
    faster = build_profile(track, speed_offsets={"T2": 3.0})
    bx, by = to_local_xy(base.lat, base.lon, ORIGIN)
    fx, fy = to_local_xy(faster.lat, faster.lon, ORIGIN)
    gap = np.hypot(fx - bx, fy - by)
    apex = track.corners[1].apex_m
    assert gap[np.searchsorted(base.d, apex)] == pytest.approx(3.0 * LINE_WIDENING_M, abs=0.01)
    assert np.hypot(fx, fy)[np.searchsorted(base.d, apex)] > np.hypot(bx, by)[np.searchsorted(base.d, apex)]  # outward
    assert gap[np.abs(base.d - apex) > 4 * CORNER_WIDTH_M].max() < 0.01  # the rest of the lap is unchanged


def test_line_offset_recovers_the_known_widening():
    from iagent.analysis.position import line_offset

    track = DEFAULT_TRACK
    base = build_profile(track)
    faster = build_profile(track, speed_offsets={"T2": 3.0})  # T2: 6 m wider at the apex
    step = 4  # coarser grid (2 m), as the UI uses
    bx, by = to_local_xy(base.lat[::step], base.lon[::step], ORIGIN)
    fx, fy = to_local_xy(faster.lat[::step], faster.lon[::step], ORIGIN)
    offset, nearest = line_offset(fx, fy, bx, by)
    apex = int(np.searchsorted(base.d[::step], track.corners[1].apex_m))
    # The synthetic circle runs anticlockwise, so "wider" (outward) is to the right: negative.
    assert offset[apex] == pytest.approx(-3.0 * LINE_WIDENING_M, abs=0.05)
    assert abs(nearest[apex] - apex) <= 1
    far = np.abs(base.d[::step] - track.corners[1].apex_m) > 4 * CORNER_WIDTH_M
    assert np.abs(offset[far]).max() < 0.05

    same, _ = line_offset(bx, by, bx, by)
    assert np.nanmax(np.abs(same)) < 1e-9
    gappy = fx.copy()
    gappy[:5] = np.nan
    off, idx = line_offset(gappy, fy, bx, by)
    assert np.isnan(off[:5]).all() and (idx[:5] == -1).all()
