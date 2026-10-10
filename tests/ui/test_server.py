import dataclasses
import json
import sys

import pytest
from starlette.testclient import TestClient

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.references.garage61 import csv_to_lap
from iagent.testing.garage61 import to_garage61_csv
from iagent.testing.synthetic import SyntheticSource
from iagent.testing.web import ui_client
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
    return ui_client(create_app(root, static_dir) if static_dir else create_app(root))


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


def test_tracks_are_empty_when_workspace_has_no_lap_store(tmp_path):
    assert client(tmp_path).get("/api/tracks").json() == []
    assert not (tmp_path / "index.sqlite").exists()


def test_ui_starts_with_an_empty_workspace_and_watches_missing_telemetry_dir(tmp_path, monkeypatch):
    import uvicorn
    from click.testing import CliRunner

    from iagent.cli import cli
    from iagent.laps import watch

    telemetry_dir = tmp_path / "telemetry"
    started = []
    monkeypatch.setattr(watch, "default_telemetry_dir", lambda: telemetry_dir)
    monkeypatch.setattr(watch.TelemetryWatcher, "start", lambda self: started.append(self.folder))

    def run(app, **kwargs):
        with ui_client(app) as web:
            assert web.get("/api/tracks").json() == []

    monkeypatch.setattr(uvicorn, "run", run)
    result = CliRunner().invoke(cli, ["--workspace", str(tmp_path / "workspace"), "ui", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert started == [telemetry_dir]
    assert not (tmp_path / "workspace" / "index.sqlite").exists()


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
    return ui_client(app)


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
    app = ui_client(create_app(root, static_dir=tmp_path / "no-build", garage61=no_token))
    res = app.get("/api/garage61/laps", params={"track": "synthetic", "car": "synthcar"})
    assert res.status_code == 200 and res.json() == {"available": False, "reason": "No Garage61 token.", "laps": []}


def test_ghosts_install_on_the_sim_pc_and_download_elsewhere(garage61, tmp_path):
    gid = "01FASTLAP000001"
    app = garage61.app
    lapfiles = tmp_path / "iRacing" / "lapfiles"
    (lapfiles / "synthetic").mkdir(parents=True)

    sim_pc = ui_client(create_app(app.state.workspace, static_dir=tmp_path / "no-build", garage61=app.state.garage61,
                                   lapfiles=lapfiles))
    assert sim_pc.get("/api/system").json()["lapfiles_found"] is True
    out = sim_pc.post("/api/garage61/ghost", json={"garage61_id": gid, "install": True}).json()
    assert out["installed"] and (lapfiles / "synthetic" / out["installed"].split("/")[-1]).exists()

    mac = ui_client(create_app(app.state.workspace, static_dir=tmp_path / "no-build", garage61=app.state.garage61,
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
    app = ui_client(create_app(root, static_dir=tmp_path / "no-build", watcher=TelemetryWatcher(root, folder)))
    telemetry = app.get("/api/system").json()["telemetry"]
    assert telemetry["found"] is True and telemetry["folder"] == str(folder) and telemetry["version"] == 0


def test_only_local_pages_can_post(workspace):
    root, _, _ = workspace
    app = create_app(root)
    assert TestClient(app, base_url="http://localhost").post("/api/chat", json={"message": "hi"}).status_code == 403
    assert TestClient(app, base_url="http://localhost").post(
        "/api/chat", content='{"message": "hi"}', headers={"Content-Type": "text/plain"}).status_code == 403
    assert TestClient(app, base_url="http://evil.example").get("/api/tracks").status_code == 400
    assert ui_client(app).get("/api/tracks").status_code == 200


def ibt_bytes(tmp_path, name="synthcar_synthetic 2026-10-04 15-00-00.ibt", laps=3) -> tuple[str, bytes]:
    from iagent.testing.ibt_writer import write_ibt

    source = SyntheticSource(n_laps=laps, seed=4)
    (tmp_path / "drop").mkdir(exist_ok=True)
    path = write_ibt(tmp_path / "drop" / name, source.session, source.frames())
    return name, path.read_bytes()


def test_ibt_files_can_be_imported_by_upload(tmp_path):
    root = tmp_path / "ws"  # a first run on a Mac: no workspace yet
    web = ui_client(create_app(root, static_dir=tmp_path / "no-build"))
    name, data = ibt_bytes(tmp_path)

    out = web.post("/api/ingest", params={"name": name}, content=data).json()
    assert out["file"] == name and out["laps"] == 3 and out["track"] == "Synthetic Test Circuit"
    assert [r["laps"] for r in web.get("/api/tracks").json()] == [3]
    assert web.post("/api/ingest", params={"name": name}, content=data).json()["skipped"] == "already ingested"
    assert not list((root / "cache").glob("upload-*"))  # the uploaded copy isn't kept

    assert web.post("/api/ingest", params={"name": "notes.txt"}, content=b"x").status_code == 400
    bad = web.post("/api/ingest", params={"name": "broken.ibt"}, content=b"not an ibt")
    assert bad.status_code == 422 and "broken.ibt" in bad.json()["error"]


def test_the_watched_folder_can_be_changed_and_rescanned(workspace, tmp_path):
    import os

    from iagent.laps.watch import TelemetryWatcher, saved_telemetry_dir

    root, _, _ = workspace
    watcher = TelemetryWatcher(root, tmp_path / "missing")
    watcher.rescan = lambda: watcher.scan(now=2000) + watcher.scan(now=2010)  # no real pause in tests
    web = ui_client(create_app(root, static_dir=tmp_path / "no-build", watcher=watcher))

    synced = tmp_path / "OneDrive" / "telemetry"
    name, data = ibt_bytes(tmp_path)
    synced.mkdir(parents=True)
    (synced / name).write_bytes(data)
    os.utime(synced / name, (1000.0, 1000.0))

    assert web.post("/api/telemetry/folder", json={"folder": str(tmp_path / "nope")}).status_code == 400
    status = web.post("/api/telemetry/folder", json={"folder": str(synced)}).json()
    assert status["folder"] == str(synced) and status["found"] is True
    assert saved_telemetry_dir(root) == synced

    out = web.post("/api/telemetry/rescan").json()
    assert out["ingested"] == [name] and out["telemetry"]["version"] == 1

    unwatched = ui_client(create_app(root, static_dir=tmp_path / "no-build"))
    assert unwatched.post("/api/telemetry/rescan").status_code == 409


def test_garage61_token_can_be_added_from_the_ui(workspace, tmp_path):
    from iagent.references.garage61 import Garage61Client
    from iagent.testing.garage61 import mock_api

    root, _, _ = workspace
    token_file = tmp_path / "config" / "garage61.token"

    def factory():
        if not token_file.exists():
            raise WorkspaceError("No Garage61 token.")
        return Garage61Client(token_file.read_text().strip(), "https://g61.test/api/v1", mock_api(9999, 999, {}))

    web = ui_client(create_app(root, static_dir=tmp_path / "no-build", garage61=factory, token_file=token_file))
    assert web.get("/api/system").json()["garage61"] == {"token": False}
    assert web.get("/api/garage61/status").json() == {"connected": False, "reason": "No Garage61 token."}

    status = web.post("/api/garage61/token", json={"token": " test-token \n"}).json()
    assert status["connected"] is True and status["user"]
    assert token_file.read_text() == "test-token\n" and (token_file.stat().st_mode & 0o777) == 0o600
    assert web.get("/api/system").json()["garage61"] == {"token": True}


def test_system_reports_whether_the_coach_can_run(workspace, tmp_path):
    from iagent.ui.coach import CoachRuns

    root, _, _ = workspace
    missing = ui_client(create_app(root, coach=CoachRuns(root, claude=str(tmp_path / "no-claude"))))
    assert missing.get("/api/system").json()["coach"]["found"] is False
    present = ui_client(create_app(root, coach=CoachRuns(root, claude=sys.executable)))
    assert present.get("/api/system").json()["coach"]["found"] is True
