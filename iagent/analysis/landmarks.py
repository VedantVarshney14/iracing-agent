"""Corner-name hints from CrewChief's track landmarks (MIT-licensed, github.com/mrbelowski/CrewChiefV4).

Coverage is partial (about 25 iRacing tracks, last updated 2019) and names are rough
("radillion", "turn9"), so these are *hints* to align with a derived corner map, not a source of
truth. Distances are metres from the start/finish line, like ours, but may come from an older
scan of the track.
"""

import json
import re
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import certifi

from iagent.analysis.corners import Corner, CornerMap

URL = "https://raw.githubusercontent.com/mrbelowski/CrewChiefV4/master/CrewChiefV4/trackLandmarksData.json"
SLACK_M = 60  # a corner matches a landmark if its apex is within the landmark's range ± this
_GENERIC = re.compile(r"^turn\s*\d+$|^t\d+$", re.I)


@dataclass(frozen=True)
class Landmark:
    name: str  # as published, e.g. "la_source"
    start_m: float
    end_m: float

    @property
    def display(self) -> str:
        return self.name.replace("_", " ").title()

    @property
    def generic(self) -> bool:
        return bool(_GENERIC.match(self.name.replace("_", " ")))


def load(cache: Path, refresh: bool = False) -> dict:
    """The landmarks file, downloaded once into `cache`."""
    if refresh or not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        # certifi's CA bundle: python.org builds on macOS ship without system certificates.
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(URL, timeout=30, context=context) as resp:  # noqa: S310
            cache.write_bytes(resp.read())
    return json.loads(cache.read_text())


def _normalise(name: str) -> str:
    """Compare track codes and our slugged keys alike, ignoring scan years that iRacing adds over
    time ("spa up", "spa 2024 up" and "spa-2024-up" all match)."""
    tokens = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    return " ".join(t for t in tokens if not re.fullmatch(r"\d{4}", t))


def find(data: dict, track_code: str) -> tuple[str, list[Landmark]] | None:
    """Landmarks for an iRacing track code (exact match first, then ignoring scan years)."""
    entries = [e for e in data.get("TrackLandmarksData", []) if e.get("irTrackName")]
    for key in (lambda n: re.sub(r"[^a-z0-9]+", " ", n.lower()).strip(), _normalise):
        for e in entries:
            if key(e["irTrackName"]) == key(track_code):
                marks = [
                    Landmark(m["landmarkName"], float(m["distanceRoundLapStart"]), float(m["distanceRoundLapEnd"]))
                    for m in e["trackLandmarks"]
                ]
                return e["irTrackName"], marks
    return None


def match(cmap: CornerMap, marks: list[Landmark]) -> list[dict]:
    """For each landmark, the derived corners whose apex falls inside its range (± slack)."""
    out = []
    for m in marks:
        hits: list[Corner] = [
            c for c in cmap.corners if m.start_m - SLACK_M <= c.apex_m <= m.end_m + SLACK_M
        ]
        out.append({
            "landmark": m.name,
            "suggested_name": None if m.generic else m.display,
            "start_m": m.start_m,
            "end_m": m.end_m,
            "corners": [c.id for c in hits],
        })
    return out
