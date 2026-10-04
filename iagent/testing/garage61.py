"""Fakes for Garage61: lap CSVs in its export format, and a mock API transport."""

import httpx
import numpy as np
import pandas as pd

from iagent.telemetry.frames import SURFACE_OFF_TRACK


def to_garage61_csv(raw: pd.DataFrame, track_length_m: float) -> str:
    """A recorded lap's raw 60 Hz frames in Garage61's CSV layout, ending with the first sample
    past the line (as real exports do)."""
    out = pd.DataFrame({
        "Speed": raw["Speed"],
        "LapDistPct": raw["LapDistPct"],
        "Lat": 0.0,
        "Lon": 0.0,
        "Brake": raw["Brake"],
        "Throttle": raw["Throttle"],
        "RPM": 7000.0,
        "SteeringWheelAngle": raw["SteeringWheelAngle"],
        "Gear": raw["Gear"].astype(int),
        "ABSActive": "false",
        "LatAccel": raw["LatAccel"],
        "LongAccel": raw.get("LongAccel", 0.0),
        "PositionType": np.where(raw["PlayerTrackSurface"] == SURFACE_OFF_TRACK, 4, 3),
    })
    last = out.iloc[-1].copy()
    last["LapDistPct"] = last["LapDistPct"] + last["Speed"] / 60.0 / track_length_m - 1.0
    out = pd.concat([out, last.to_frame().T], ignore_index=True)
    return out.to_csv(index=False)


def fake_blap(driver: str = "Fast Friend", car_path: str = "synthcar", track_path: str = "synthetic\\full") -> bytes:
    """Bytes shaped like an iRacing .blap header: magic, driver name at 16, then car and track."""
    header = bytearray(1600)
    header[0:8] = b"BLAP\x03\x00\x00\x00"
    header[16:16 + len(driver)] = driver.encode()
    header[144:144 + len(car_path)] = car_path.encode()
    header[1278:1278 + len(car_path)] = car_path.encode()
    header[1342:1342 + len(track_path)] = track_path.encode()
    return bytes(header) + bytes(range(256)) * 4


def mock_api(track_id: int, car_id: int, laps: dict[str, tuple[dict, str]]) -> httpx.MockTransport:
    """A Garage61 API with one user ("me"), one team, and the given laps ({id: (meta, csv)}).
    Laps whose meta has `ghostAvailable` serve a fake .blap ghost."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != "Bearer test-token":
            return httpx.Response(401, json={"message": "Bad authentication"})
        path = request.url.path.removeprefix("/api/v1")
        if path == "/me":
            return httpx.Response(200, json={"slug": "me-driver"})
        if path == "/teams":
            return httpx.Response(200, json={"items": [{"slug": "fast-friends", "name": "Fast Friends"}]})
        if path == "/tracks":
            return httpx.Response(200, json={"items": [
                {"id": 1, "platform": "iracing", "platform_id": "1"},
                {"id": 444, "platform": "iracing", "platform_id": str(track_id)},
            ]})
        if path == "/cars":
            return httpx.Response(200, json={"items": [{"id": 127, "platform": "iracing", "platform_id": str(car_id)}]})
        if path == "/laps":
            q = request.url.params
            if q.get("tracks") != "444" or q.get("cars") != "127" or "fast-friends" not in q.get("teams", ""):
                return httpx.Response(200, json={"items": [], "total": 0})
            items = [meta for meta, _ in laps.values()]
            return httpx.Response(200, json={"items": items, "total": len(items)})
        for lap_id, (meta, csv) in laps.items():
            if path == f"/laps/{lap_id}":
                return httpx.Response(200, json=meta)
            if path == f"/laps/{lap_id}/csv":
                return httpx.Response(200, text=csv)
            if path == f"/laps/{lap_id}/ghost.bin" and meta.get("ghostAvailable"):
                return httpx.Response(200, content=fake_blap())
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler)


def lap_meta(lap_id: str, lap_time: float, track_id: int, car_id: int, slug: str = "fast-friend") -> dict:
    return {
        "id": lap_id,
        "driver": {"slug": slug, "firstName": "Fast", "lastName": "Friend"},
        "driverRating": 2500,
        "sessionType": 1,
        "startTime": "2026-09-01T20:00:00Z",
        "lapNumber": 3,
        "lapTime": lap_time,
        "clean": True,
        "trackTemp": 31.2,
        "trackUsage": 60,
        "canViewTelemetry": True,
        "ghostAvailable": True,
        "track": {"id": 444, "name": "Synthetic Test Circuit", "platform": "iracing", "platform_id": str(track_id)},
        "car": {"id": 127, "name": "Synthetic Car", "platform": "iracing", "platform_id": str(car_id)},
    }


__all__ = ["fake_blap", "lap_meta", "mock_api", "to_garage61_csv"]
