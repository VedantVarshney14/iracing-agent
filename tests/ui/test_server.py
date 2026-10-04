import dataclasses
import json

import pytest
from starlette.testclient import TestClient

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.references.garage61 import csv_to_lap
from iagent.testing.garage61 import to_garage61_csv
from iagent.testing.synthetic import SyntheticSource
from iagent.ui.server import create_app
from iagent.workspace import WorkspaceError


@pytest.fixture
def workspace(tmp_path):
    source = SyntheticSource(n_laps=5, seed=3)
    store = ParquetLapStore(tmp_path)
    records = record(source, store, "synthetic")
    store.close()
    return tmp_path, source, records


def client(root, static_dir=None) -> TestClient:
    return TestClient(create_app(root, static_dir) if static_dir else create_app(root))


def add_reference(root, source, rec, driver: str):
    """Import one of our laps as if it were a teammate's Garage61 lap."""
    store = ParquetLapStore(root)
    raw = store.load(rec.lap_id, grid=False)
    store.close()
    session = dataclasses.replace(source.session, session_id=f"g61-{driver.lower()}")
    lap = csv_to_lap(to_garage61_csv(raw, source.session.track_length_m), session, rec.lap_time)
    refs = ParquetLapStore(root / "reference")
    saved = refs.save(lap, "garage61")
    refs.close()
    meta = root / "reference" / "meta" / f"{saved.lap_id}.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"driver": driver, "date": "2026-09-01", "garage61_id": "01X"}))
    return saved


def test_tracks(workspace):
    root, _, _ = workspace
    rows = client(root).get("/api/tracks").json()
    assert [(r["track"], r["car"]) for r in rows] == [("synthetic", "synthcar")]
    assert rows[0]["laps"] == 5 and rows[0]["reference_laps"] == 0


def test_laps_default_to_the_best_lap_against_the_next_best(workspace):
    root, _, records = workspace
    data = client(root).get("/api/laps", params={"track": "synthetic", "car": "synthcar"}).json()
    timed = sorted((r for r in records if r.valid), key=lambda r: r.lap_time)
    assert data["default_lap"] == timed[0].lap_id
    assert data["default_ghost"] == timed[1].lap_id
    assert len(data["laps"]) == 5 and data["references"] == []


def test_a_garage61_teammate_is_the_default_ghost(workspace):
    root, source, records = workspace
    slowest = max((r for r in records if r.valid), key=lambda r: r.lap_time)
    ref = add_reference(root, source, slowest, "Teammate")
    data = client(root).get("/api/laps", params={"track": "synthetic", "car": "synthcar"}).json()
    assert data["default_ghost"] == ref.lap_id
    assert data["references"][0]["driver"] == "Teammate"

    review = client(root).get("/api/review", params={"lap": data["default_lap"]}).json()
    assert review["ref"]["lap_id"] == ref.lap_id
    assert review["ref"]["reference"] and review["ref"]["driver"] == "Teammate"


def test_review_payload(workspace):
    root, source, records = workspace
    lap, ref = [r for r in records if r.valid][:2]
    data = client(root).get("/api/review", params={"lap": lap.lap_id, "ref": ref.lap_id}).json()

    assert data["lap"]["lap_id"] == lap.lap_id and data["ref"]["lap_id"] == ref.lap_id
    assert data["total_delta_s"] == pytest.approx(lap.lap_time - ref.lap_time, abs=1e-3)
    corners = data["corners"]
    assert [c["label"] for c in corners] == ["T1", "T2", "T3", "T4"]
    assert sum(c["delta_s"] for c in corners) == pytest.approx(data["total_delta_s"], abs=0.01)

    trace = data["trace"]
    n = len(trace["distance_m"])
    assert n == int(source.session.track_length_m / 2)
    assert len(trace["gap_s"]) == n and len(trace["lap"]["Speed"]) == n and len(trace["ref"]["Brake"]) == n
    assert max(trace["lap"]["Speed"]) > 150  # km/h
    assert trace["gap_s"][-1] == pytest.approx(data["total_delta_s"], abs=0.05)

    pos = data["position"]
    assert len(pos["lap"]["x"]) == n and len(pos["ref"]["y"]) == n
    assert max(pos["ref"]["x"]) - min(pos["ref"]["x"]) == pytest.approx(2 * 3000 / 6.2832, rel=0.02)  # the circle


def test_errors_are_json(workspace):
    root, _, _ = workspace
    c = client(root)
    missing = c.get("/api/review", params={"lap": "nope"})
    assert missing.status_code == 404 and "Unknown lap" in missing.json()["error"]
    assert c.get("/api/laps").status_code == 400


def test_unbuilt_ui_explains_how_to_build(workspace, tmp_path):
    root, _, _ = workspace
    page = client(root, static_dir=tmp_path / "no-build").get("/")
    assert page.status_code == 200 and "npm --prefix web run build" in page.text


@pytest.fixture
def garage61(tmp_path):
    """A workspace ingested through the CLI (so track ids are recorded) and a mock Garage61 with
    one teammate lap: the driver's own lap 2, renamed."""
    from click.testing import CliRunner

    from iagent.cli import cli
    from iagent.references.garage61 import Garage61Client
    from iagent.testing.garage61 import lap_meta, mock_api

    root = tmp_path / "ws"
    assert CliRunner().invoke(cli, ["--workspace", str(root), "ingest", "synthetic", "--laps", "4", "--seed", "5"]).exit_code == 0
    store = ParquetLapStore(root)
    rec = [r for r in store.list() if r.valid][1]
    csv = to_garage61_csv(store.load(rec.lap_id, grid=False), 3000.0)
    store.close()
    transport = mock_api(9999, 999, {"01FASTLAP000001": (lap_meta("01FASTLAP000001", rec.lap_time, 9999, 999), csv)})
    factory = lambda: Garage61Client("test-token", "https://g61.test/api/v1", transport)
    app = create_app(root, static_dir=tmp_path / "no-build", garage61=factory)
    app.state.workspace, app.state.garage61 = root, factory
    return TestClient(app)


def test_garage61_laps_can_be_listed_and_imported_as_ghosts(garage61):
    params = {"track": "synthetic", "car": "synthcar"}
    found = garage61.get("/api/garage61/laps", params=params).json()
    assert found["available"] and [l["garage61_id"] for l in found["laps"]] == ["01FASTLAP000001"]
    assert found["laps"][0]["driver"] == "Fast Friend" and found["laps"][0]["lap_id"] is None

    imported = garage61.post("/api/garage61/import", json={"garage61_id": "01FASTLAP000001"}).json()
    lap_id = imported["lap_id"]
    assert garage61.get("/api/garage61/laps", params=params).json()["laps"][0]["lap_id"] == lap_id
    again = garage61.post("/api/garage61/import", json={"garage61_id": "01FASTLAP000001"}).json()
    assert again["lap_id"] == lap_id  # not downloaded twice

    laps = garage61.get("/api/laps", params=params).json()
    assert laps["default_ghost"] == lap_id and laps["references"][0]["driver"] == "Fast Friend"


def test_garage61_without_a_token_is_reported_not_fatal(workspace, tmp_path):
    def no_token():
        raise WorkspaceError("No Garage61 token.")

    root, _, _ = workspace
    app = TestClient(create_app(root, static_dir=tmp_path / "no-build", garage61=no_token))
    res = app.get("/api/garage61/laps", params={"track": "synthetic", "car": "synthcar"})
    assert res.status_code == 200 and res.json() == {"available": False, "reason": "No Garage61 token.", "laps": []}


def test_ghosts_install_on_the_sim_pc_and_download_elsewhere(garage61, tmp_path):
    gid = "01FASTLAP000001"
    app = garage61.app
    lapfiles = tmp_path / "iRacing" / "lapfiles"
    (lapfiles / "synthetic").mkdir(parents=True)

    sim_pc = TestClient(create_app(app.state.workspace, static_dir=tmp_path / "no-build", garage61=app.state.garage61,
                                   lapfiles=lapfiles))
    assert sim_pc.get("/api/system").json()["lapfiles_found"] is True
    out = sim_pc.post("/api/garage61/ghost", json={"garage61_id": gid, "install": True}).json()
    assert out["installed"] and (lapfiles / "synthetic" / out["installed"].split("/")[-1]).exists()

    mac = TestClient(create_app(app.state.workspace, static_dir=tmp_path / "no-build", garage61=app.state.garage61,
                                lapfiles=tmp_path / "no-iracing"))
    assert mac.get("/api/system").json()["lapfiles_found"] is False
    out = mac.post("/api/garage61/ghost", json={"garage61_id": gid}).json()
    assert out["installed"] is None
    blap = mac.get(out["download"])
    assert blap.status_code == 200 and blap.content.startswith(b"BLAP")
    assert mac.get("/api/ghost-file", params={"name": "../../index.sqlite"}).status_code == 404


def test_system_reports_the_telemetry_watcher(workspace, tmp_path):
    from iagent.laps.watch import TelemetryWatcher

    root, _, _ = workspace
    folder = tmp_path / "telemetry"
    folder.mkdir()
    app = TestClient(create_app(root, static_dir=tmp_path / "no-build", watcher=TelemetryWatcher(root, folder)))
    telemetry = app.get("/api/system").json()["telemetry"]
    assert telemetry["found"] is True and telemetry["folder"] == str(folder) and telemetry["version"] == 0
