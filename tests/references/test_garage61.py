import json

import pytest
from click.testing import CliRunner

from iagent import cli as cli_module
from iagent.analysis.corners import corner_metrics, derive_corner_map
from iagent.cli import cli
from iagent.laps.recorder import record
from iagent.laps.resample import resample_to_distance
from iagent.laps.store import ParquetLapStore
from iagent.references.garage61 import Garage61Client, Garage61Error, csv_to_lap, g61_id_for
from iagent.testing.garage61 import lap_meta, mock_api, to_garage61_csv
from iagent.testing.synthetic import LapKind, SyntheticSource

TRACK_ID, CAR_ID = 9999, 999


@pytest.fixture
def driven(tmp_path):
    """A synthetic session in a lap store: (source, store, records)."""
    source = SyntheticSource(n_laps=4, seed=5)
    store = ParquetLapStore(tmp_path / "laps")
    records = record(source, store, "synthetic")
    yield source, store, records
    store.close()


def test_csv_round_trip_keeps_time_and_corner_metrics(driven):
    source, store, records = driven
    rec = records[2]
    csv = to_garage61_csv(store.load(rec.lap_id, grid=False), source.session.track_length_m)

    measured = csv_to_lap(csv, source.session, lap_time=None)
    assert measured.valid and measured.lap_time == pytest.approx(rec.lap_time, abs=0.02)

    imported = csv_to_lap(csv, source.session, lap_time=rec.lap_time)
    grids = [store.load(r.lap_id) for r in records if r.valid]
    cmap = derive_corner_map(grids, "synthetic", "synthcar", [])
    original = corner_metrics(store.load(rec.lap_id), rec.lap_time, cmap)
    copied = corner_metrics(resample_to_distance(imported), imported.lap_time, cmap)
    for a, b in zip(original, copied):
        assert b["brake_m"] == pytest.approx(a["brake_m"], abs=2)
        assert b["min_speed_kph"] == pytest.approx(a["min_speed_kph"], abs=1)
        assert b["time_s"] == pytest.approx(a["time_s"], abs=0.03)


def test_csv_off_track_and_flags(tmp_path):
    source = SyntheticSource(n_laps=3, kinds=[LapKind.CLEAN, LapKind.OFF_TRACK, LapKind.CLEAN], seed=1)
    store = ParquetLapStore(tmp_path)
    rec = record(source, store, "x")[1]
    lap = csv_to_lap(to_garage61_csv(store.load(rec.lap_id, grid=False), 3000.0), source.session, rec.lap_time)
    store.close()
    assert lap.valid and lap.off_track_s == pytest.approx(1.0, abs=0.1)
    assert "ABSActive" in lap.frames and set(lap.frames["ABSActive"].unique()) == {0.0}


def test_csv_keeps_position_when_the_export_has_it(driven):
    source, store, records = driven
    raw = store.load(records[2].lap_id, grid=False)
    lap = csv_to_lap(to_garage61_csv(raw, 3000.0), source.session, records[2].lap_time)
    assert lap.frames["Lat"].to_numpy() == pytest.approx(raw["Lat"].to_numpy(), abs=1e-9)

    no_gps = to_garage61_csv(raw.drop(columns=["Lat", "Lon"]), 3000.0)  # exported as zeros
    assert "Lat" not in csv_to_lap(no_gps, source.session, records[2].lap_time).frames


def test_rejects_non_garage61_csv(driven):
    source, _, _ = driven
    with pytest.raises(ValueError, match="Not a Garage61"):
        csv_to_lap("a,b\n1,2\n", source.session, None)


def test_client_errors_are_readable():
    client = Garage61Client("wrong", base_url="https://g61.test/api/v1", transport=mock_api(1, 1, {}))
    with pytest.raises(Garage61Error, match="401"):
        client.me()


def test_g61_ids_map_from_iracing_ids():
    items = [{"id": 7, "platform": "iracing", "platform_id": "523"}, {"id": 8, "platform": "other", "platform_id": "523"}]
    assert g61_id_for(items, 523) == 7 and g61_id_for(items, 1) is None and g61_id_for(items, None) is None


@pytest.fixture
def run(tmp_path, monkeypatch, driven):
    """CLI against a workspace with the synthetic session ingested and a mock Garage61 that has
    one faster teammate lap (the driver's own lap 2, renamed)."""
    source, store, records = driven
    ref = records[2]
    csv = to_garage61_csv(store.load(ref.lap_id, grid=False), source.session.track_length_m)
    transport = mock_api(TRACK_ID, CAR_ID, {"01FASTLAP000001": (lap_meta("01FASTLAP000001", ref.lap_time, TRACK_ID, CAR_ID), csv)})
    monkeypatch.setattr(cli_module, "_garage61_client",
                        lambda: Garage61Client("test-token", "https://g61.test/api/v1", transport))
    runner = CliRunner()

    def invoke(*args):
        return runner.invoke(cli, ["--workspace", str(tmp_path / "ws"), *args], catch_exceptions=False)

    assert invoke("ingest", "synthetic", "--laps", "4", "--seed", "5").exit_code == 0
    return invoke


def test_find_and_import_a_teammate_lap(run):
    status = json.loads(run("garage61", "status", "--json").output)
    assert status["teams"][0]["slug"] == "fast-friends"

    found = json.loads(run("garage61", "find", "--track", "synthetic", "--json").output)
    assert [lap["garage61_id"] for lap in found["laps"]] == ["01FASTLAP000001"]
    assert found["laps"][0]["can_view_telemetry"] is True and "vs_your_best_pct" in found["laps"][0]

    (imported,) = json.loads(run("garage61", "import", "01FASTLAP000001", "--json").output)
    assert imported["same_car_as_yours"] and imported["valid"]
    assert imported["track"] == "synthetic" and imported["car"] == "synthcar"

    refs = json.loads(run("refs", "list", "--json").output)
    assert refs[0]["driver"] == "Fast Friend" and refs[0]["lap_id"] == imported["lap_id"]

    # Reference laps never count as the driver's own.
    own = json.loads(run("laps", "list", "--json").output)
    assert imported["lap_id"] not in {lap["lap_id"] for lap in own}

    mine = next(lap["lap_id"] for lap in own if lap["valid"])
    compared = json.loads(run("corners", "compare", mine, imported["lap_id"], "--json").output)
    assert compared["ref"] == imported["lap_id"] and len(compared["corners"]) == 4


def test_import_needs_a_recorded_track(run, monkeypatch, driven):
    source, store, records = driven
    meta = lap_meta("01ELSEWHERE00001", 60.0, 1234, CAR_ID)
    transport = mock_api(1234, CAR_ID, {"01ELSEWHERE00001": (meta, "Speed,LapDistPct\n1,0\n")})
    monkeypatch.setattr(cli_module, "_garage61_client",
                        lambda: Garage61Client("test-token", "https://g61.test/api/v1", transport))
    result = run("garage61", "import", "01ELSEWHERE00001")
    assert result.exit_code != 0 and "haven't recorded" in result.output


def test_missing_token_explains_setup(tmp_path, monkeypatch):
    for name in ("GARAGE61_TOKEN", "GARAGE61_PAT", "GARAGE_61_PAT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setattr("iagent.references.garage61.TOKEN_FILE", tmp_path / "none")
    result = CliRunner().invoke(cli, ["garage61", "status"])
    assert result.exit_code != 0 and "GARAGE61_TOKEN" in result.output


def test_token_from_dotenv_reads_only_garage61_keys(tmp_path, monkeypatch):
    from iagent.references.garage61 import find_token

    for name in ("GARAGE61_TOKEN", "GARAGE61_PAT", "GARAGE_61_PAT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("iagent.references.garage61.TOKEN_FILE", tmp_path / "none")
    env = tmp_path / ".env"
    env.write_text("OTHER_SECRET=nope\nexport GARAGE61_PAT='abc123'\n")
    assert find_token(env) == "abc123"
    env.write_text("OTHER_SECRET=nope\n")
    assert find_token(env) is None


def test_ghost_download_and_install(run, tmp_path):
    lapfiles = tmp_path / "iRacing" / "lapfiles"
    (lapfiles / "synthetic").mkdir(parents=True)
    out = json.loads(run("garage61", "ghost", "01FASTLAP000001", "--install", "--lapfiles", str(lapfiles), "--json").output)
    assert out["track_path"] == "synthetic\\full" and out["car_path"] == "synthcar"
    installed = lapfiles / "synthetic" / out["installed"].split("/")[-1]
    assert installed.read_bytes().startswith(b"BLAP")
    assert out["saved"].endswith(".blap")


def test_ghost_install_without_iracing_explains(run, tmp_path):
    result = run("garage61", "ghost", "01FASTLAP000001", "--install", "--lapfiles", str(tmp_path / "missing"))
    assert result.exit_code != 0 and "lapfiles folder" in result.output and "saved at" in result.output
