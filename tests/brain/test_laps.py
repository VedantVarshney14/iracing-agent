import pytest

from iagent.brain.laps import (
    LapSegmenter,
    REASON_DISCONTINUITY,
    REASON_INCOMPLETE,
    REASON_PIT_ROAD,
)
from iagent.common.frames import Frame


def segment(source):
    seg = LapSegmenter(source.session)
    laps = [lap for f in source.frames() if (lap := seg.push(f)) is not None]
    tail = seg.flush()
    if tail:
        laps.append(tail)
    return laps


def test_clean_laps_match_ground_truth_times(clean_source):
    first, *laps = segment(clean_source)
    assert len(laps) == 3
    # No start/finish crossing was observed before it, so the first lap is never timed.
    assert first.reasons == [REASON_INCOMPLETE] and first.lap_time is None
    for lap, truth in zip(laps, clean_source.truth[1:]):
        assert lap.valid, lap.reasons
        # Sub-frame accuracy: the crossing is interpolated between frames (frame = 16.7 ms).
        assert lap.lap_time == pytest.approx(truth.lap_time, abs=1e-3)
        assert lap.start_time == pytest.approx(truth.start_time, abs=1e-3)


def test_messy_laps_are_flagged_for_the_right_reason(messy_source):
    laps = segment(messy_source)
    assert len(laps) == 6
    expected = [
        {REASON_INCOMPLETE},
        set(),  # off-track is information, not a reason
        {REASON_PIT_ROAD},
        {REASON_PIT_ROAD},
        {REASON_DISCONTINUITY},
        set(),
    ]
    assert [set(lap.reasons) for lap in laps] == expected
    assert [lap.valid for lap in laps] == [t.expect_valid for t in messy_source.truth]


def test_off_track_time_is_measured_but_does_not_invalidate(messy_source):
    off_lap = segment(messy_source)[1]
    assert off_lap.valid
    assert off_lap.off_track_s == pytest.approx(1.0, abs=0.1)  # 60 m at ~60 m/s
    assert segment(messy_source)[5].off_track_s == 0.0


def test_partial_first_lap_has_no_lap_time(messy_source):
    first = segment(messy_source)[0]
    assert not first.complete
    assert first.lap_time is None


def test_flagged_laps_keep_their_time_when_complete(messy_source):
    laps = segment(messy_source)
    # Pit laps are complete laps with a time, just not valid ones.
    assert laps[2].complete and laps[2].lap_time == pytest.approx(messy_source.truth[2].lap_time, abs=1e-3)


def test_tiny_tail_is_dropped(clean_source):
    # The source's closing frame (one sample past the line) must not become a junk lap.
    assert len(segment(clean_source)) == 4  # one lap per generated lap, no extra stub


def test_frames_out_of_world_are_ignored(clean_source):
    seg = LapSegmenter(clean_source.session)
    assert seg.push(Frame(0.0, {"SessionTime": 0.0, "LapDistPct": -1.0})) is None
    assert seg.flush() is None


def test_missing_position_channel_is_an_error(clean_source):
    with pytest.raises(ValueError, match="LapDistPct"):
        LapSegmenter(clean_source.session).push(Frame(0.0, {"SessionTime": 0.0}))


def test_lap_ids_are_unique_and_ordered(clean_source):
    ids = [lap.lap_id for lap in segment(clean_source)]
    assert ids == sorted(set(ids))
