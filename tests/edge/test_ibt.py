import pytest

from iagent.brain.recorder import record
from iagent.brain.store import ParquetLapStore
from iagent.edge.ibt import IbtSource
from iagent.testing.ibt_writer import write_ibt


@pytest.fixture
def ibt_path(tmp_path, messy_source):
    return write_ibt(tmp_path / "spa-test.ibt", messy_source.session, messy_source.frames())


def test_session_info_is_read_from_the_file(ibt_path, messy_source):
    session = IbtSource(ibt_path).session
    assert session.track_name == messy_source.session.track_name
    assert session.track_length_m == pytest.approx(messy_source.session.track_length_m)
    assert session.car_name == "Synthetic Car"
    assert session.session_id == "spa-test"


def test_frames_round_trip(ibt_path, messy_source):
    original = list(messy_source.frames())
    replayed = list(IbtSource(ibt_path).frames())
    assert len(replayed) == len(original)
    for a, b in list(zip(original, replayed))[::500]:
        assert b.session_time == pytest.approx(a.session_time)
        assert b.values["Speed"] == pytest.approx(a.values["Speed"], rel=1e-5)
        assert b.values["Gear"] == a.values["Gear"]
        assert b.values["OnPitRoad"] == a.values["OnPitRoad"]


def test_replayed_file_gives_the_same_laps_as_the_generator(ibt_path, messy_source, tmp_path):
    direct = record(messy_source, ParquetLapStore(tmp_path / "a"), "direct")
    via_file = record(IbtSource(ibt_path), ParquetLapStore(tmp_path / "b"), "file")
    assert [r.reasons for r in via_file] == [r.reasons for r in direct]
    for a, b in zip(direct, via_file):
        assert (a.lap_time is None) == (b.lap_time is None)
        if a.lap_time is not None:
            assert b.lap_time == pytest.approx(a.lap_time, abs=5e-3)  # float32 in the file


def test_requested_channel_subset_is_respected(ibt_path):
    frame = next(IbtSource(ibt_path, channels=["SessionTime", "LapDistPct", "Speed"]).frames())
    assert set(frame.values) == {"SessionTime", "LapDistPct", "Speed"}


def test_file_without_required_channels_is_rejected(tmp_path, clean_source):
    path = write_ibt(tmp_path / "x.ibt", clean_source.session, clean_source.frames(), channels=["SessionTime", "Speed"])
    with pytest.raises(ValueError, match="LapDistPct"):
        next(IbtSource(path).frames())


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        IbtSource("/nope/missing.ibt")
