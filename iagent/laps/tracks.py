"""Per-track facts learned from recordings: iRacing ids, names, length and the cars driven.

Stored as `<workspace>/tracks/<track_key>/track.json` and updated on every ingest. Used where a
lap has to be matched to the outside world (Garage61 looks laps up by iRacing track and car id)
and where a track's length is needed without one of its recordings at hand.
"""

import json
from pathlib import Path

from iagent.telemetry.session import SessionInfo


def info_path(workspace: Path, track_key: str) -> Path:
    return workspace / "tracks" / track_key / "track.json"


def load_track_info(workspace: Path, track_key: str) -> dict | None:
    path = info_path(workspace, track_key)
    return json.loads(path.read_text()) if path.exists() else None


def update_track_info(workspace: Path, session: SessionInfo) -> dict:
    info = load_track_info(workspace, session.track_key) or {"cars": {}}
    info.update({
        "track_key": session.track_key,
        "track_id": session.track_id,
        "track_code": session.track_code,
        "name": session.track_name,
        "config": session.track_config,
        "length_m": session.track_length_m,
    })
    info["cars"][session.car_key] = {"car_id": session.car_id, "name": session.car_name}
    path = info_path(workspace, session.track_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, indent=2) + "\n")
    return info
