import json

import pytest
from click.testing import CliRunner

from iagent.cli import cli


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
