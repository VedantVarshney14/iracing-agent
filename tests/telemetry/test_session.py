import pytest

from iagent.telemetry.session import parse_session_yaml

# Deliberately not strictly valid YAML (unquoted colon in a name), like real session info.
SPA = """---
WeekendInfo:
 TrackID: 163
 TrackName: spa up
 TrackDisplayName: Circuit de Spa-Francorchamps
 TrackConfigName: Grand Prix Pits: Long
 TrackLength: 6.94 km
DriverInfo:
 DriverCarIdx: 3
 Drivers:
 - CarIdx: 0
   UserName: Someone Else
   CarScreenName: Ferrari 296 GT3
   CarPath: ferrari296gt3
 - CarIdx: 3
   UserName: Vedant
   CarScreenName: Porsche 911 GT3 R (992)
   CarPath: porsche992rgt3
   CarID: 169
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


def test_keys_use_internal_names_so_layouts_and_cars_stay_apart():
    info = parse_session_yaml(SPA)
    assert info.track_key == "spa-up"
    assert info.track_config == "Grand Prix Pits: Long"
    assert info.car_key == "porsche992rgt3"
    assert info.car_id == 169


def test_keys_fall_back_to_display_names():
    info = parse_session_yaml("WeekendInfo:\n TrackDisplayName: Some Track\n TrackLength: 2.0 km\n")
    assert info.track_key == "some-track" and info.car_key == "unknown"


def test_empty_value_does_not_swallow_the_next_line():
    text = "WeekendInfo:\n TrackConfigName: \n TrackLength: 2.0 km\n"
    assert parse_session_yaml(text).track_config is None
