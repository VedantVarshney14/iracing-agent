import pytest

from iagent.analysis.compare import MS_TO_KPH, compare, summarize, trace
from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.testing.synthetic import LapKind, SyntheticSource


@pytest.fixture
def laps(tmp_path):
    """Laps 1 and 3 are clean, lap 2 is driven uniformly slower. Lap 0 is the partial first lap."""
    source = SyntheticSource(n_laps=4, kinds=[LapKind.CLEAN, LapKind.CLEAN, LapKind.SLOW, LapKind.CLEAN])
    store = ParquetLapStore(tmp_path)
    records = record(source, store, "synthetic")
    yield store, records, source.truth
    store.close()


def test_splits_add_up_to_the_lap_time(laps):
    store, records, _ = laps
    rec = records[1]
    out = summarize(store.load(rec.lap_id), rec.lap_time, sections=10)
    assert sum(s["time_s"] for s in out["splits"]) == pytest.approx(rec.lap_time, abs=0.01)
    assert out["splits"][0]["start_m"] == 0 and out["splits"][-1]["end_m"] == 3000


def test_split_brake_point_and_min_speed_match_ground_truth(laps):
    store, records, truth = laps
    rec = records[1]
    out = summarize(store.load(rec.lap_id), rec.lap_time, sections=10)
    t1 = out["splits"][1]  # 300-600 m contains T1's braking zone
    assert t1["brake_start_m"] == pytest.approx(truth[1].brake_m["T1"], abs=3)
    t1_apex = out["splits"][2]  # T1's apex is at 600 m
    assert t1_apex["min_speed_kph"] == pytest.approx(truth[1].min_speed["T1"] * MS_TO_KPH, abs=2)


def test_lap_against_itself_has_no_delta(laps):
    store, records, _ = laps
    rec = records[1]
    grid = store.load(rec.lap_id)
    out = compare(grid, rec.lap_time, grid, rec.lap_time)
    assert out["total_delta_s"] == 0
    assert all(s["delta_s"] == 0 for s in out["sections"])
    assert out["biggest_losses"] == []


def test_slow_lap_loses_time_and_sections_sum_to_total(laps):
    store, records, _ = laps
    slow, ref = records[2], records[1]
    out = compare(store.load(slow.lap_id), slow.lap_time, store.load(ref.lap_id), ref.lap_time)
    assert out["total_delta_s"] == pytest.approx(slow.lap_time - ref.lap_time, abs=0.001)
    assert out["total_delta_s"] > 5
    assert sum(s["delta_s"] for s in out["sections"]) == pytest.approx(out["total_delta_s"], abs=0.02)
    assert len(out["biggest_losses"]) == 3


def test_trace_samples_a_range_in_kph(laps):
    store, records, _ = laps
    grid = store.load(records[1].lap_id)
    df = trace(grid, ["Speed", "Brake"], start_m=400, end_m=700, step_m=10)
    assert list(df.columns) == ["LapDist", "lap_time_s", "Speed", "Brake"]
    assert df["LapDist"].iloc[0] == 400 and df["LapDist"].iloc[-1] == 700
    assert len(df) == 31
    assert df["Speed"].max() > 150  # km/h, not m/s
    with pytest.raises(KeyError, match="not in this lap"):
        trace(grid, ["Nope"])


def test_brake_start_is_an_onset_not_a_section_edge(laps):
    store, records, truth = laps
    rec = records[1]
    # Boundary at 500 m falls inside T1's braking zone (starts ~480 m): the section starting at
    # 500 m must not claim a brake start at its own edge.
    out = summarize(store.load(rec.lap_id), rec.lap_time, sections=6)
    t1_zone = [s for s in out["splits"] if s["start_m"] <= truth[1].brake_m["T1"] < s["end_m"]]
    assert t1_zone[0]["brake_start_m"] == pytest.approx(truth[1].brake_m["T1"], abs=3)
    after = [s for s in out["splits"] if s["start_m"] == 500][0]
    assert after["brake_start_m"] != 500
