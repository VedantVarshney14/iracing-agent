"""Finding Garage61 laps for a track and car, and importing them as reference laps.

Shared by the CLI (`iagent garage61 find/import`) and the web UI's ghost picker. Problems the
driver can fix are `WorkspaceError`s; Garage61's own failures are `Garage61Error`s.
"""

import json
from typing import Callable

from iagent.laps.pace import best_times
from iagent.laps.tracks import load_track_info
from iagent.references import garage61 as g61
from iagent.telemetry.session import SessionInfo
from iagent.workspace import Workspace, WorkspaceError

NO_TOKEN = (
    "No Garage61 token. Create a personal access token at https://garage61.net/developer, then set "
    f"GARAGE61_TOKEN or save it to {g61.TOKEN_FILE}."
)


def client_from_env() -> g61.Garage61Client:
    token = g61.find_token()
    if not token:
        raise WorkspaceError(NO_TOKEN)
    return g61.Garage61Client(token)


def cached(ws: Workspace, name: str, fetch: Callable[[], list[dict]]) -> list[dict]:
    """Garage61's track/car catalogues change rarely: cache them in the workspace."""
    path = ws.root / "cache" / f"garage61-{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    items = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items))
    return items


def summarize(lap: dict) -> dict:
    driver = lap.get("driver") or {}
    return {
        "garage61_id": lap.get("id"),
        "driver": f"{driver.get('firstName', '')} {driver.get('lastName', '')}".strip() or driver.get("slug"),
        "driver_slug": driver.get("slug"),
        "lap_time": lap.get("lapTime"),
        "driver_rating": lap.get("driverRating"),
        "date": (lap.get("startTime") or "")[:10],
        "session_type": {1: "practice", 2: "qualifying", 3: "race"}.get(lap.get("sessionType")),
        "track_temp_c": round(lap["trackTemp"], 1) if lap.get("trackTemp") else None,
        "track_usage_pct": lap.get("trackUsage"),
        "clean": lap.get("clean"),
        "can_view_telemetry": lap.get("canViewTelemetry"),
        "ghost_available": lap.get("ghostAvailable"),
    }


def all_track_info(ws: Workspace) -> list[dict]:
    root = ws.root / "tracks"
    return [json.loads(p.read_text()) for p in sorted(root.glob("*/track.json"))] if root.exists() else []


def imported(ws: Workspace) -> dict[str, str]:
    """Garage61 lap id -> reference lap id, for laps already in the workspace."""
    meta_dir = ws.root / "reference" / "meta"
    out = {}
    for path in sorted(meta_dir.glob("*.json")) if meta_dir.exists() else []:
        gid = json.loads(path.read_text()).get("garage61_id")
        if gid:
            out[gid] = path.stem
    return out


def find(ws: Workspace, client: g61.Garage61Client, track: str, car: str | None,
         teams: tuple[str, ...] = (), limit: int = 20) -> dict:
    """Best Garage61 lap per driver (the driver's own and teammates'), fastest first."""
    info = load_track_info(ws.root, track)
    if not info or not info.get("track_id"):
        raise WorkspaceError(f"No iRacing ids recorded for {track}. Re-run `iagent ingest` on a recording of it.")
    cars = info.get("cars", {})
    if car is None:
        if len(cars) != 1:
            raise WorkspaceError(f"Pick a car with --car: {', '.join(cars) or 'none recorded'}.")
        car = next(iter(cars))
    if car not in cars or not cars[car].get("car_id"):
        raise WorkspaceError(f"No iRacing car id recorded for {car} on {track}.")

    g_track = g61.g61_id_for(cached(ws, "tracks", client.tracks), info["track_id"])
    g_car = g61.g61_id_for(cached(ws, "cars", client.cars), cars[car]["car_id"])
    if g_track is None or g_car is None:
        raise WorkspaceError("Garage61 doesn't list this track or car.")
    team_slugs = list(teams) or [t["slug"] for t in client.teams() if t.get("slug")]
    found = client.find_laps(g_track, g_car, team_slugs, limit)

    own_best = best_times(ws.store().list(track=track, car=car)).get((track, car))
    already = imported(ws)
    rows = []
    for lap in found:
        row = summarize(lap)
        if own_best and row["lap_time"]:
            row["vs_your_best_pct"] = round((row["lap_time"] / own_best - 1) * 100, 2)
        row["lap_id"] = already.get(row["garage61_id"])
        rows.append(row)
    return {"track": track, "car": car, "your_best": own_best, "laps": rows}


def import_lap(ws: Workspace, client: g61.Garage61Client, gid: str) -> dict:
    """Download one Garage61 lap's telemetry and store it as a reference lap."""
    meta = client.lap(gid)
    summary = summarize(meta)
    track_pid = int((meta.get("track") or {}).get("platform_id") or 0)
    car_pid = int((meta.get("car") or {}).get("platform_id") or 0)
    info = next((i for i in all_track_info(ws) if i.get("track_id") == track_pid), None)
    if info is None:
        raise WorkspaceError(
            f"{gid} is on {(meta.get('track') or {}).get('name')} (iRacing track {track_pid}), "
            "which you haven't recorded. Ingest one of your own laps there first."
        )
    car_key = next((k for k, c in info["cars"].items() if c.get("car_id") == car_pid), None)
    car_name = (meta.get("car") or {}).get("name", "unknown")
    session = SessionInfo(
        track_name=info["name"],
        track_length_m=info["length_m"],
        car_name=car_name,
        track_id=info["track_id"],
        session_id=f"g61-{summary['driver_slug'] or 'driver'}-{gid[-6:].lower()}",
        track_code=info.get("track_code"),
        track_config=info.get("config"),
        car_path=car_key or car_name,
        car_id=car_pid,
    )
    # The download itself is the authority: Garage61's per-lap `canViewTelemetry` flag has been
    # seen to say False for laps whose CSV downloads fine.
    try:
        csv_text = client.lap_csv(gid)
    except g61.Garage61Error as e:
        raise WorkspaceError(f"Couldn't download the telemetry of {gid}: {e}") from e
    try:
        lap = g61.csv_to_lap(csv_text, session, meta.get("lapTime"), meta.get("lapNumber"))
    except ValueError as e:
        raise WorkspaceError(str(e)) from e
    rec = ws.refs(create=True).save(lap, "garage61")
    path = ws.meta_path(rec.lap_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n")
    return {**summary, "lap_id": rec.lap_id, "track": rec.track_key, "car": rec.car_key,
            "valid": rec.valid, "same_car_as_yours": car_key is not None}
