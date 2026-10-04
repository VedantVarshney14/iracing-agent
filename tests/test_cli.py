import json

import pytest
from click.testing import CliRunner

from iagent.cli import DEFAULT_WORKSPACE, cli


@pytest.fixture
def run(tmp_path):
    runner = CliRunner()

    def invoke(*args: str):
        result = runner.invoke(cli, ["--workspace", str(tmp_path), *args], catch_exceptions=False)
        return result

    return invoke


@pytest.fixture
def ingested(run):
    result = run("ingest", "synthetic", "--laps", "6", "--messy")
    assert result.exit_code == 0, result.output
    return result


def test_ingest_reports_every_kind_of_lap(ingested):
    out = ingested.output
    assert "pit_road" in out and "discontinuity" in out and "incomplete" in out


def test_commands_need_a_workspace(run):
    result = run("laps", "list")
    assert result.exit_code != 0 and "iagent ingest" in result.output


def test_tracks_json(run, ingested):
    (row,) = json.loads(run("tracks", "--json").output)
    assert (row["track"], row["car"]) == ("synthetic", "synthcar")
    assert row["laps"] == 6 and row["valid_laps"] == 2 and row["representative_laps"] == 2


def test_laps_list_json_and_filters(run, ingested):
    laps = json.loads(run("laps", "list", "--json").output)
    assert len(laps) == 6
    assert {"lap_id", "lap_time", "valid", "reasons", "off_track_s", "vs_best_pct"} <= set(laps[0])
    valid = json.loads(run("laps", "list", "--valid-only", "--json").output)
    assert all(lap["valid"] for lap in valid) and len(valid) == 2
    assert json.loads(run("laps", "list", "--track", "elsewhere", "--json").output) == []
    assert run("laps", "list", "--representative").exit_code == 0


def test_laps_show_and_compare_default_reference(run, ingested):
    valid = json.loads(run("laps", "list", "--valid-only", "--json").output)
    a, b = valid[0]["lap_id"], valid[1]["lap_id"]

    shown = json.loads(run("laps", "show", a, "--json").output)
    assert len(shown["splits"]) == 10 and shown["top_speed_kph"] > 150

    compared = json.loads(run("laps", "compare", a, "--json").output)
    assert compared["ref"] == b  # the fastest *other* valid lap
    assert len(compared["sections"]) == 20
    assert run("laps", "compare", a).exit_code == 0  # human-readable table


def test_laps_trace_is_csv(run, ingested):
    lap = json.loads(run("laps", "list", "--valid-only", "--json").output)[0]["lap_id"]
    out = run("laps", "trace", lap, "--channels", "Speed,Brake", "--from", "400", "--to", "500").output
    lines = out.strip().splitlines()
    assert lines[0] == "LapDist,lap_time_s,Speed,Brake" and len(lines) == 12


def test_unknown_lap_is_a_clean_error(run, ingested):
    result = run("laps", "show", "nope")
    assert result.exit_code != 0 and "Unknown lap" in result.output


def test_workspace_reports_paths(run, tmp_path):
    info = json.loads(run("workspace", "--json").output)
    assert info["workspace"] == str(tmp_path.resolve()) and info["has_laps"] is False


def test_default_workspace_is_relative_to_project_not_current_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["workspace", "--json"], catch_exceptions=False)
    info = json.loads(result.output)

    assert info["workspace"] == str(DEFAULT_WORKSPACE.resolve())
    assert info["workspace"] != str((tmp_path / "workspace").resolve())


def test_corners_are_mapped_on_first_use_and_can_be_named(run, ingested, tmp_path):
    listed = json.loads(run("corners", "list", "--track", "synthetic", "--json").output)
    assert len(listed["corners"]) == 4
    assert (tmp_path / "tracks" / "synthetic" / "corners.json").exists()

    assert run("corners", "name", "--track", "synthetic", "1", "Turn One").exit_code == 0
    remapped = json.loads(run("corners", "map", "--track", "synthetic", "--json").output)
    assert remapped["corners"][0]["name"] == "Turn One"  # names survive re-mapping
    assert remapped["corners"][0]["name_source"] == "driver"

    assert run("corners", "name", "--track", "synthetic", "1", "--clear").exit_code == 0
    assert run("corners", "name", "--track", "synthetic", "9", "Nope").exit_code != 0


def test_corner_report_compare_and_consistency(run, ingested):
    valid = json.loads(run("laps", "list", "--valid-only", "--json").output)
    a = valid[0]["lap_id"]
    report = json.loads(run("corners", "report", a, "--json").output)
    assert len(report["corners"]) == 4 and report["corners"][0]["brake_m"] is not None
    compared = json.loads(run("corners", "compare", a, "--json").output)
    assert compared["ref"] == valid[1]["lap_id"]
    assert sum(c["delta_s"] for c in compared["corners"]) == pytest.approx(compared["total_delta_s"], abs=0.01)
    spread = json.loads(run("corners", "consistency", "--track", "synthetic", "--json").output)
    assert len(spread["laps"]) == 2 and spread["corners"][0]["brake_spread_m"] is not None
    for cmd in (["corners", "report", a], ["corners", "compare", a], ["corners", "consistency", "--track", "synthetic"]):
        assert run(*cmd).exit_code == 0  # human-readable tables


def test_corner_landmarks_from_cache(run, ingested, tmp_path):
    cache = tmp_path / "cache" / "crewchief-landmarks.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"TrackLandmarksData": [{"irTrackName": "synthetic", "trackLandmarks": [
        {"landmarkName": "first_bend", "distanceRoundLapStart": 560, "distanceRoundLapEnd": 640},
        {"landmarkName": "turn2", "distanceRoundLapStart": 1150, "distanceRoundLapEnd": 1250},
    ]}]}))
    out = json.loads(run("corners", "landmarks", "--track", "synthetic", "--apply", "--json").output)
    assert out["applied"] == [1]  # the generic "turn2" is not applied
    listed = json.loads(run("corners", "list", "--track", "synthetic", "--json").output)
    assert listed["corners"][0]["name"] == "First Bend" and listed["corners"][0]["name_source"] == "crewchief"
