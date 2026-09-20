"""Session metadata (track, car, ...) parsed from iRacing's session-info YAML."""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionInfo:
    track_name: str
    track_length_m: float
    car_name: str = "unknown"
    track_id: int | None = None
    # Free-form origin label, e.g. the .ibt file stem or "synthetic-7". Used in lap ids.
    session_id: str = "session"


def _first(pattern: str, text: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip().strip("'\"") if m else None


def parse_session_yaml(text: str, session_id: str = "session") -> SessionInfo:
    """Pull the few fields we need out of the session-info YAML with regexes.

    iRacing's YAML is frequently not strictly valid (unquoted colons etc.), and a full parse
    would buy us nothing here, so we deliberately don't use a YAML loader.
    """
    length_km = _first(r"^\s*TrackLength:\s*([\d.]+)\s*km", text, re.M)
    if length_km is None:
        raise ValueError("Session info has no TrackLength; cannot build a distance grid.")

    track_name = _first(r"^\s*TrackDisplayName:\s*(.+)$", text, re.M) or "unknown"
    track_id = _first(r"^\s*TrackID:\s*(\d+)", text, re.M)

    car_name = "unknown"
    idx = _first(r"^\s*DriverCarIdx:\s*(\d+)", text, re.M)
    if idx is not None:
        block = re.search(
            rf"-\s*CarIdx:\s*{idx}\s*\n(.*?)(?=\n\s*-\s*CarIdx:|\Z)", text, re.S
        )
        if block:
            car_name = _first(r"^\s*CarScreenName:\s*(.+)$", block.group(1), re.M) or car_name

    return SessionInfo(
        track_name=track_name,
        track_length_m=float(length_km) * 1000.0,
        car_name=car_name,
        track_id=int(track_id) if track_id else None,
        session_id=session_id,
    )
