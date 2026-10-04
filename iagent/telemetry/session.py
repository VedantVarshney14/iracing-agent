"""Session metadata (track, car, ...) parsed from iRacing's session-info YAML."""

import re
from dataclasses import dataclass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


@dataclass(frozen=True, slots=True)
class SessionInfo:
    track_name: str  # display name, e.g. "Circuit de Spa-Francorchamps" (same for every layout)
    track_length_m: float
    car_name: str = "unknown"  # display name, e.g. "FIA F4"
    track_id: int | None = None
    # Free-form origin label, e.g. the .ibt file stem or "synthetic-7". Used in lap ids.
    session_id: str = "session"
    track_code: str | None = None  # iRacing's internal TrackName, e.g. "spa 2024 up": one per layout
    track_config: str | None = None  # e.g. "Grand Prix"
    car_path: str | None = None  # iRacing's internal car id, e.g. "formulair04"
    car_id: int | None = None  # iRacing's numeric car id, e.g. 148

    @property
    def track_key(self) -> str:
        """Stable key for grouping laps: laps on different keys must never be compared."""
        return slug(self.track_code or self.track_name)

    @property
    def car_key(self) -> str:
        return slug(self.car_path or self.car_name)


def _first(pattern: str, text: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    value = m.group(1).strip().strip("'\"") if m else ""
    return value or None


def parse_session_yaml(text: str, session_id: str = "session") -> SessionInfo:
    """Pull the few fields we need out of the session-info YAML with regexes.

    iRacing's YAML is frequently not strictly valid (unquoted colons etc.), and a full parse
    would buy us nothing here, so we deliberately don't use a YAML loader.
    """
    length_km = _first(r"^\s*TrackLength:[ \t]*([\d.]+)\s*km", text, re.M)
    if length_km is None:
        raise ValueError("Session info has no TrackLength; cannot build a distance grid.")

    track_name = _first(r"^\s*TrackDisplayName:[ \t]*(.+)$", text, re.M) or "unknown"
    track_id = _first(r"^\s*TrackID:[ \t]*(\d+)", text, re.M)
    track_code = _first(r"^\s*TrackName:[ \t]*(.+)$", text, re.M)
    track_config = _first(r"^\s*TrackConfigName:[ \t]*(.+)$", text, re.M)

    car_name = "unknown"
    car_path = None
    car_id = None
    idx = _first(r"^\s*DriverCarIdx:[ \t]*(\d+)", text, re.M)
    if idx is not None:
        block = re.search(
            rf"-\s*CarIdx:\s*{idx}\s*\n(.*?)(?=\n\s*-\s*CarIdx:|\Z)", text, re.S
        )
        if block:
            car_name = _first(r"^\s*CarScreenName:[ \t]*(.+)$", block.group(1), re.M) or car_name
            car_path = _first(r"^\s*CarPath:[ \t]*(.+)$", block.group(1), re.M)
            car_id = _first(r"^\s*CarID:[ \t]*(\d+)", block.group(1), re.M)

    return SessionInfo(
        track_name=track_name,
        track_length_m=float(length_km) * 1000.0,
        car_name=car_name,
        track_id=int(track_id) if track_id else None,
        session_id=session_id,
        track_code=track_code,
        track_config=track_config,
        car_path=car_path,
        car_id=int(car_id) if car_id else None,
    )
