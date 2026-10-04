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
