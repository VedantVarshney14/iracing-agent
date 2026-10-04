"""iRacing ghost (comparison) lap files, as served by Garage61, and installing them for iRacing.

A ghost is iRacing's own `.blap` best-lap file (magic `BLAP`). Its header carries the driver name,
the car (`formulair04`) and iRacing's track path (`okayama\\full`). iRacing keeps these under
`Documents/iRacing/lapfiles/<track>/` and loads any of them from the in-sim menu: Options ->
Driving Aids -> Load Comparison Lap, with "Display Reference Car" ticked for the ghost car.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"BLAP"
_PRINTABLE = re.compile(rb"[ -~]{3,}")


@dataclass(frozen=True)
class GhostInfo:
    driver: str | None
    car_path: str | None
    track_path: str | None  # e.g. "okayama\\full"


def read_info(data: bytes) -> GhostInfo:
    if not data.startswith(MAGIC):
        raise ValueError("Not an iRacing ghost lap (.blap) file.")
    strings = [m.group().decode("ascii") for m in _PRINTABLE.finditer(data[:4096])]
    driver = data[16:80].split(b"\0", 1)[0].decode("latin-1") or None
    track = next((s for s in strings if re.fullmatch(r"[a-z0-9 _.-]+\\[a-z0-9 _.-]+", s)), None)
    car = None
    if track is not None:
        before = strings[: strings.index(track)]
        car = next((s for s in reversed(before) if re.fullmatch(r"[a-z0-9_]+", s)), None)
    return GhostInfo(driver, car, track)


def default_lapfiles() -> Path:
    """iRacing's lapfiles folder: IAGENT_IRACING_LAPFILES, else Documents/iRacing/lapfiles
    (checking a OneDrive-redirected Documents folder too, common on Windows)."""
    if os.environ.get("IAGENT_IRACING_LAPFILES"):
        return Path(os.environ["IAGENT_IRACING_LAPFILES"])
    home = Path.home()
    for docs in (home / "Documents", home / "OneDrive" / "Documents"):
        if (docs / "iRacing").exists():
            return docs / "iRacing" / "lapfiles"
    return home / "Documents" / "iRacing" / "lapfiles"


def install_dir(lapfiles: Path, info: GhostInfo) -> Path:
    """Where iRacing will list this ghost: the existing folder for its track if iRacing has made
    one (`<track>/<config>` or `<track>`), otherwise a new `<track>` folder."""
    if not info.track_path:
        raise ValueError("The ghost file doesn't say which track it's for; use an explicit folder.")
    parts = info.track_path.split("\\")
    for candidate in (lapfiles.joinpath(*parts), lapfiles / parts[0]):
        if candidate.is_dir():
            return candidate
    return lapfiles / parts[0]


def file_name(driver_slug: str, car_path: str | None, lap_time: float | None) -> str:
    """Distinct from iRacing's own `<custid>_<car>.blap`, so the driver's own best lap is never
    overwritten."""
    time = f"{lap_time:.3f}".replace(".", "_") if lap_time else "lap"
    name = f"g61_{driver_slug}_{car_path or 'car'}_{time}.blap"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name)
