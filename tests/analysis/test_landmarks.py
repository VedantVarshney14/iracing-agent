from iagent.analysis.corners import Corner, CornerMap
from iagent.analysis.landmarks import find, match

DATA = {"TrackLandmarksData": [
    {"rf2TrackNames": ["Spa"], "trackLandmarks": []},
    {"irTrackName": "spa up", "trackLandmarks": [
        {"landmarkName": "la_source", "distanceRoundLapStart": 360, "distanceRoundLapEnd": 430},
        {"landmarkName": "turn9", "distanceRoundLapStart": 2600, "distanceRoundLapEnd": 2700},
        {"landmarkName": "kemmel", "distanceRoundLapStart": 1580, "distanceRoundLapEnd": 1710},
    ]},
]}


def _corner(i, apex):
    return Corner(i, "R", apex - 40, apex, apex + 40, 0, 0, 100.0, False)


def test_finds_tracks_ignoring_scan_years_and_slugs():
    assert find(DATA, "spa up")[0] == "spa up"
    assert find(DATA, "spa 2024 up")[0] == "spa up"
    assert find(DATA, "spa-2024-up")[0] == "spa up"
    assert find(DATA, "okayama full") is None


def test_match_suggests_names_but_not_generic_ones():
    cmap = CornerMap("spa-2024-up", "car", 6929, [], [_corner(1, 392), _corner(2, 2616)])
    _, marks = find(DATA, "spa-2024-up")
    by_name = {m["landmark"]: m for m in match(cmap, marks)}
    assert by_name["la_source"]["corners"] == [1] and by_name["la_source"]["suggested_name"] == "La Source"
    assert by_name["turn9"]["corners"] == [2] and by_name["turn9"]["suggested_name"] is None
    assert by_name["kemmel"]["corners"] == []
