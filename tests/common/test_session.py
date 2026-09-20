import pytest

from iagent.common.session import parse_session_yaml

# Deliberately not strictly valid YAML (unquoted colon in a name), like real session info.
SPA = """---
WeekendInfo:
 TrackID: 163
 TrackDisplayName: Circuit de Spa-Francorchamps
 TrackConfigName: Grand Prix Pits: Long
 TrackLength: 6.94 km
DriverInfo:
 DriverCarIdx: 3
 Drivers:
 - CarIdx: 0
   UserName: Someone Else
   CarScreenName: Ferrari 296 GT3
 - CarIdx: 3
   UserName: Vedant
   CarScreenName: Porsche 911 GT3 R (992)
...
"""


def test_parses_track_length_name_and_id():
    info = parse_session_yaml(SPA, session_id="s1")
    assert info.track_name == "Circuit de Spa-Francorchamps"
    assert info.track_length_m == pytest.approx(6940.0)
    assert info.track_id == 163
    assert info.session_id == "s1"


def test_finds_the_drivers_own_car_not_the_first_listed():
    assert parse_session_yaml(SPA).car_name == "Porsche 911 GT3 R (992)"


def test_missing_track_length_is_an_error():
    with pytest.raises(ValueError, match="TrackLength"):
        parse_session_yaml("WeekendInfo:\n TrackDisplayName: X\n")


def test_missing_optional_fields_fall_back():
    info = parse_session_yaml("WeekendInfo:\n TrackLength: 2.0 km\n")
    assert info.car_name == "unknown" and info.track_name == "unknown" and info.track_id is None
