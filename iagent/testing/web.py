"""A test client for the `iagent ui` server, sending what the real web app sends."""

from starlette.testclient import TestClient


def ui_client(app) -> TestClient:
    return TestClient(app, base_url="http://localhost", headers={"X-Iagent": "1"})
