from click.testing import CliRunner

from iagent.cli import cli


def test_replay_synthetic_then_list(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["replay", "synthetic", "--laps", "6", "--messy", "--store", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "pit_road" in result.output and "discontinuity" in result.output and "incomplete" in result.output

    listed = runner.invoke(cli, ["laps", "--store", str(tmp_path), "--valid-only"])
    assert listed.exit_code == 0
    representative = runner.invoke(cli, ["laps", "--store", str(tmp_path), "--representative"])
    assert representative.exit_code == 0, representative.output

    rows = [line for line in listed.output.splitlines() if line.startswith("synthetic-")]
    assert rows and all("True" in row for row in rows)
