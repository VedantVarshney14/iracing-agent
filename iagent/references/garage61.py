"""Reference laps from Garage61 (https://garage61.net).

What a personal access token can reach (per Garage61's API docs): laps driven by the token's
user and their Garage61 teammates. Searching all visible laps needs an application approved by
Garage61. Whether a lap's telemetry can be downloaded is reported per lap (`canViewTelemetry`) and
depends on plan and privacy settings.

Telemetry comes as a CSV of iRacing channels at 60 Hz, one lap per file, with no time column:
time is rebuilt from the sample index.
"""

import io
import os
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from iagent.analysis.position import has_position
from iagent.laps.segment import REASON_INCOMPLETE, REASON_PIT_ROAD, Lap
from iagent.telemetry.frames import SURFACE_OFF_TRACK, SURFACE_ON_TRACK
from iagent.telemetry.session import SessionInfo

BASE_URL = "https://garage61.net/api/v1"
TOKEN_ENV = ("GARAGE61_TOKEN", "GARAGE61_PAT", "GARAGE_61_PAT")
TOKEN_FILE = Path.home() / ".config" / "iagent" / "garage61.token"
CSV_HZ = 60.0

# PositionType in the CSV: 0 unknown, 1 pit lane, 2 pit stop, 3 on track, 4 off track.
_POSITION_PIT = (1, 2)
_POSITION_OFF_TRACK = 4


class Garage61Error(RuntimeError):
    pass


def _from_dotenv(path: Path) -> str | None:
    """Only the Garage61 token keys are read from a .env file; nothing else is loaded."""
    if not path.is_file():
        return None
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if sep and key.strip() in TOKEN_ENV:
            values[key.strip()] = value.strip().strip("'\"")
    return next((values[k] for k in TOKEN_ENV if values.get(k)), None)


def find_token(dotenv: Path | None = None) -> str | None:
    """The token from the environment, a `.env` file in the working directory, or TOKEN_FILE."""
    for name in TOKEN_ENV:
        if os.environ.get(name):
            return os.environ[name].strip()
    token = _from_dotenv(dotenv or Path.cwd() / ".env")
    if token:
        return token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip() or None
    return None


def save_token(token: str, path: Path | None = None) -> Path:
    """Save a personal access token to TOKEN_FILE (readable by the user only)."""
    path = path or TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token.strip() + "\n")
    path.chmod(0o600)  # if it already existed with wider permissions
    return path


class Garage61Client:
    def __init__(self, token: str, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        resp = self._http.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        if resp.status_code == 401:
            raise Garage61Error("Garage61 rejected the token (401). Check GARAGE61_TOKEN.")
        if resp.status_code == 429:
            raise Garage61Error("Garage61 rate limit hit (429). Try again in a minute.")
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise Garage61Error(f"Garage61 {path} failed ({resp.status_code}): {message}")
        return resp

    def me(self) -> dict:
        return self._get("/me").json()

    def teams(self) -> list[dict]:
        return self._get("/teams").json().get("items", [])

    def tracks(self) -> list[dict]:
        return self._get("/tracks").json().get("items", [])

    def cars(self) -> list[dict]:
        return self._get("/cars").json().get("items", [])

    def find_laps(self, track_id: int, car_id: int | None, teams: list[str], limit: int = 20,
                  everyone: bool = False) -> list[dict]:
        """Best lap per driver on a track (and car), fastest first.

        `teams` are team slugs whose members to include, along with your own laps. `everyone`
        drops the driver filter: only works for applications Garage61 approved for that.
        """
        params = {
            "tracks": track_id,
            "cars": car_id,
            "group": "driver",
            "lapTypes": 1,
            "limit": limit,
        }
        if not everyone:
            params["drivers"] = "me"
            if teams:
                params["teams"] = ",".join(teams)
        items = self._get("/laps", params).json().get("items", [])
        return sorted(items, key=lambda lap: lap.get("lapTime") or float("inf"))

    def lap(self, lap_id: str) -> dict:
        return self._get(f"/laps/{lap_id}").json()

    def lap_csv(self, lap_id: str) -> str:
        return self._get(f"/laps/{lap_id}/csv").text

    def ghost(self, lap_id: str) -> bytes:
        """The lap's iRacing ghost file (`.blap`), when `ghostAvailable` is true."""
        return self._get(f"/laps/{lap_id}/ghost.bin").content


def g61_id_for(items: list[dict], platform_id: int | None) -> int | None:
    """Garage61's own id for an iRacing track or car id."""
    if platform_id is None:
        return None
    for item in items:
        if item.get("platform") == "iracing" and str(item.get("platform_id")) == str(platform_id):
            return int(item["id"])
    return None


def csv_to_lap(text: str, session: SessionInfo, lap_time: float | None, sim_lap: int | None = None) -> Lap:
    """One Garage61 CSV lap as a `Lap` on `session`'s track (its length sets the distance scale).

    Rows are 60 Hz samples starting just after the line. Time zero is placed on the line by
    extrapolating the first sample back at its speed; the lap ends at `lap_time` (from the lap's
    metadata) or, without it, by extrapolating the last sample forward.
    """
    df = pd.read_csv(io.StringIO(text))
    if "LapDistPct" not in df or "Speed" not in df:
        raise ValueError(f"Not a Garage61 lap CSV: needs LapDistPct and Speed, got {list(df.columns)[:8]}")
    for col in df.columns:
        if df[col].dtype == object:  # "true"/"false" flags
            df[col] = df[col].astype(str).str.lower().map({"true": 1.0, "false": 0.0})
    # Slower channels can leave empty cells between their samples: fill along the lap.
    df = df.apply(pd.to_numeric, errors="coerce").interpolate(limit_direction="both")

    length = session.track_length_m
    # Exports can carry a sample from either side of the lap: drop any leading rows from the end
    # of the previous lap, and stop at the first wrap past the line (the next lap's first sample),
    # which pins down the crossing time.
    pct_all = df["LapDistPct"].to_numpy()
    first = 0
    while first < len(df) - 1 and pct_all[first] > 0.5 and pct_all[first + 1] < pct_all[first]:
        first += 1
    wraps = np.where(np.diff(pct_all[first:]) < -0.5)[0]
    stop = first + int(wraps[0]) + 1 if len(wraps) else len(df)
    t_all = (np.arange(len(df)) - first) / CSV_HZ

    df = df.iloc[first:stop].reset_index(drop=True)
    speed = df["Speed"].clip(lower=1.0).to_numpy()
    pct = df["LapDistPct"].to_numpy()
    t0 = pct[0] * length / speed[0]  # time from the line to the first sample
    t = t_all[first:stop] + t0
    if stop < len(pct_all):  # crossed the line: interpolate where between the two samples
        to_line = 1.0 - pct_all[stop - 1]
        frac = to_line / (to_line + pct_all[stop])
        measured_end = float(t_all[stop - 1] + t0 + frac / CSV_HZ)
    else:
        measured_end = float(t[-1] + (1.0 - pct[-1]) * length / speed[-1])
    end = lap_time if lap_time else measured_end

    # Keep the car's position when the export has it (otherwise the columns are all zeros).
    frames = df.copy() if has_position(df) else df.drop(columns=[c for c in ("Lat", "Lon") if c in df])
    frames["SessionTime"] = t
    frames["lap_time_s"] = t
    frames["LapDist"] = pct * length

    reasons: list[str] = []
    off_track_s = 0.0
    if "PositionType" in df:
        position = df["PositionType"].round()
        frames["PlayerTrackSurface"] = np.where(position == _POSITION_OFF_TRACK, SURFACE_OFF_TRACK, SURFACE_ON_TRACK)
        frames["OnPitRoad"] = position.isin(_POSITION_PIT).astype(float)
        off_track_s = float((position == _POSITION_OFF_TRACK).sum()) / CSV_HZ
        if frames["OnPitRoad"].any():
            reasons.append(REASON_PIT_ROAD)
        frames = frames.drop(columns="PositionType")
    if pct[0] > 0.01 or pct[-1] < 0.99:
        reasons.append(REASON_INCOMPLETE)

    return Lap(
        session=session,
        seq=0,
        frames=frames,
        start_time=0.0,
        end_time=end if REASON_INCOMPLETE not in reasons else None,
        reasons=reasons,
        sim_lap=sim_lap,
        off_track_s=off_track_s,
    )
