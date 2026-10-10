"""Sharing the driver's ear with CrewChief.

Many iRacing drivers run CrewChief, and it talks a lot: the spotter (cars alongside), lap times
and personal bests, sector and gap reports, position, fuel, tyres, flags, pit-lane and limiter
calls, damage, penalties and session time. The coach doesn't do any of that. It does what
CrewChief can't: technique, corner by corner, against a reference lap.

Where the two could overlap or talk over each other:

- **The spotter:** nothing new is started while `CarLeftRight` shows a car alongside (the speech
  arbiter's hold), so the coach never talks over "car left".
- **Lap times:** CrewChief reads the lap time at the line, so with CrewChief running the coach's
  lap summary leaves the time and the gap out and says only the coaching part (where the lap
  went), and for a few seconds after the line only corner cues are said.
- **Rules:** a rule that reads lap times or announces pit and flag events duplicates CrewChief;
  `iagent rules add` warns about those (see `overlaps`).

`running()` tells whether CrewChief is up (on the sim PC); `iagent live run --crewchief auto`
(the default) uses it.
"""

import subprocess
import sys

PROCESS = "crewchiefv4"

# What CrewChief already says in iRacing: the coach leaves these to it.
CREWCHIEF_COVERS = (
    "spotter (cars alongside)", "lap times and personal bests", "sector times and gaps",
    "race position", "fuel", "tyres", "flags", "pit lane, limiter and pit window", "damage",
    "penalties", "session time remaining",
)


def running() -> bool:
    """True if CrewChief is running on this machine (Windows only; elsewhere False)."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return PROCESS in out.lower()


def resolve(setting: str | bool | None) -> bool:
    """"on"/"off"/"auto" (or a bool) -> whether to coach alongside CrewChief."""
    if isinstance(setting, bool):
        return setting
    if setting in (None, "auto"):
        return running()
    return setting == "on"


def overlaps(rule) -> str | None:
    """Why a rule would duplicate CrewChief, or None."""
    names = set(rule.condition.names) if rule.condition else set()
    says = [a for a in rule.actions if a.key == "say"]
    for a in says:
        names |= a.template.names
    if rule.trigger == "pit" and says:
        return "CrewChief already calls pit entry and exit."
    said = {n for a in says for n in a.template.names}
    if said & {"lap_time", "gap_s", "best_gap_s", "best_lap"}:
        return "CrewChief already reads lap times and gaps; say what it can't (where the lap went)."
    if rule.trigger == "lap" and "new_best" in names and says:
        return "CrewChief already announces personal bests."
    return None
