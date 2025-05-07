import pytest

from iagent import telemetry


@pytest.fixture
def telemetry_client(tmp_path, ir):
    return telemetry.TelemetryCollectionClient(
        db_path=tmp_path / "telemetry.csv",
        ir=ir
    )


def test_insert_frame(telemetry_client):
    telemetry_client.insert_frame()
    assert len(telemetry_client.db.all())
