"""Cue plans: what to say approaching each corner, built from the corner map and a reference lap.

A plan is saved per track and car (`tracks/<track>/cues/<car>.json`) so the coach (or the driver)
can rewrite any cue's text before a session; rebuilding keeps rewritten text. Each cue has a
`target_m`: the point where it must have *finished*, normally the reference lap's brake point (or
the turn-in for corners taken without braking). The live coach starts it early enough to finish
there at the car's current speed.

Each cue has a full text, said while the driver learns the corner, and a short one for once
they've heard it ("La Source. Hard brake."): a coach doesn't repeat the whole description every
lap. Rewriting a cue's text without giving a short form drops the template's short form.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from iagent.analysis.corners import Corner, CornerMap, corner_metrics
from iagent.laps.lap import Lap
from iagent.workspace import Workspace, WorkspaceError

BEFORE_TURN_IN_M = 30  # a no-brake corner's cue ends this far before the corner starts
MERGE_GAP_M = (
    150  # a corner this close after the previous one's exit is cued together with it
)
MERGE_MAX = 2  # corners per cue: longer cues come too late
HARD_BRAKE, MEDIUM_BRAKE = 0.8, 0.5  # reference brake pressure (0-1)
LIFT_THROTTLE = 0.6  # a no-brake corner whose throttle dips below this is a lift
logger = logging.getLogger("iagent.live")

PLAN_VERSION = 2  # 2: cues have short forms
GEARS = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
}


@dataclass
class Cue:
    """One corner cue: the corners it covers, the point it's timed to (the reference brake point, or
    just before turn-in), its words, and a short form for once it's been heard."""

    corner: int  # the first corner it covers
    corners: list[int]  # every corner it covers (a chicane is one cue)
    target_m: float
    text: str
    source: str = "template"  # "template", or who rewrote it: "coach", "driver"
    short: str | None = (
        None  # said once the full text has been heard (None: always the full text)
    )


@dataclass
class CuePlan:
    """The corner cues for a track and car, built from a reference lap, with that lap's corner
    metrics for judging each corner after it."""

    track_key: str
    car_key: str
    ref_lap_id: str
    length_m: float
    cues: list[Cue]
    ref_metrics: list[
        dict
    ]  # the reference lap's corner metrics, for feedback after each corner
    ref_lap_time: float | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    version: int = PLAN_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "CuePlan":
        """A plan from its saved JSON; a plan from before short forms reads as version 1."""
        raw = json.loads(text)
        raw.setdefault("version", 1)  # before short forms
        raw["cues"] = [Cue(**c) for c in raw["cues"]]
        return cls(**raw)

    def cue_for(self, corner: int) -> Cue | None:
        return next((c for c in self.cues if corner in c.corners), None)


def plan_path(root: Path, track_key: str, car_key: str) -> Path:
    return root / "tracks" / track_key / "cues" / f"{car_key}.json"


def load_plan(root: Path, track_key: str, car_key: str) -> CuePlan | None:
    path = plan_path(root, track_key, car_key)
    return CuePlan.from_json(path.read_text()) if path.exists() else None


def save_plan(root: Path, plan: CuePlan) -> Path:
    """Write the plan to the workspace; returns its path."""
    path = plan_path(root, plan.track_key, plan.car_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.to_json() + "\n")
    return path


def reference_lap(
    ws: Workspace, track: str, car: str, ref_id: str | None = None
) -> Lap:
    """The lap cues follow: REF_ID, else the fastest valid Garage61 lap, else the driver's best."""
    if ref_id:
        rec = ws.find(ref_id)
        if (rec.track_key, rec.car_key) != (track, car):
            raise WorkspaceError(
                f"{ref_id} is {rec.track_key}/{rec.car_key}, not {track}/{car}."
            )
        return rec
    for store in (
        ws.refs(),
        ws.store() if (ws.root / "index.sqlite").exists() else None,
    ):
        timed = [
            r
            for r in (
                store.list(track=track, car=car, valid_only=True) if store else []
            )
            if r.lap_time
        ]
        if timed:
            return min(timed, key=lambda r: r.lap_time)
    raise WorkspaceError(
        f"No lap on {track} in {car} to build cues from: drive a few laps, or import a teammate's "
        "lap with `iagent garage61 find` / `import`."
    )


def build_plan(
    ws: Workspace, track: str, car: str, ref_id: str | None = None
) -> CuePlan:
    """A new plan from the reference lap (REF_ID, or the default): a cue per corner from its
    metrics, close corners merged into one cue, and cues someone rewrote kept."""
    ref = reference_lap(ws, track, car, ref_id)
    cmap = ws.corner_map(track)
    metrics = corner_metrics(ws.load(ref.lap_id), ref.lap_time, cmap)
    cues = merge_close(
        cmap, [approach_cue(c, m) for c, m in zip(cmap.corners, metrics)]
    )
    plan = CuePlan(track, car, ref.lap_id, cmap.length_m, cues, metrics, ref.lap_time)
    if (old := load_plan(ws.root, track, car)) is not None:
        keep_rewritten(old, plan)
    return plan


def approach_cue(c: Corner, m: dict) -> Cue:
    """A corner's cue from the reference lap's metrics: its name and direction, then how to brake
    (hard, medium or light, and the gear), or flat or a lift for a corner without braking.
    """
    name = c.name or f"Turn {c.id}"
    side = "left" if c.direction == "L" else "right"
    gear = GEARS.get(m.get("min_gear") or 0)
    brake_m, peak = m.get("brake_m"), m.get("brake_peak") or 0.0
    if m.get("missing"):
        return Cue(c.id, [c.id], c.entry_m - BEFORE_TURN_IN_M, f"{name}, {side}.")
    if brake_m is None:
        lift = (m.get("min_throttle") or 1.0) < LIFT_THROTTLE
        text = f"{name}. Lift, {side}." if lift else f"{name}. Flat, {side}."
        return Cue(
            c.id,
            [c.id],
            c.entry_m - BEFORE_TURN_IN_M,
            text,
            short=f"{name}. {'Lift' if lift else 'Flat'}.",
        )
    strength = (
        "Hard" if peak >= HARD_BRAKE else "Medium" if peak >= MEDIUM_BRAKE else "Light"
    )
    hairpin = (m.get("min_speed_kph") or 999) < 90 and (
        m.get("entry_speed_kph") or 0
    ) > 150
    shape = f"hairpin {side}" if hairpin else side
    text = f"{name}, {shape}. {strength} brake" + (f", {gear} gear." if gear else ".")
    return Cue(c.id, [c.id], float(brake_m), text, short=f"{name}. {strength} brake.")


def merge_close(cmap: CornerMap, cues: list[Cue]) -> list[Cue]:
    """A corner that follows the previous one too closely to cue on its own (a chicane, a quick
    left-right) is added to the previous cue: "Bus Stop, right. Hard brake, second gear. Then left.".
    """
    by_id = {c.id: c for c in cmap.corners}
    out: list[Cue] = []
    for cue in cues:
        prev = out[-1] if out else None
        close = (
            prev is not None
            and cue.target_m - by_id[prev.corners[-1]].exit_m < MERGE_GAP_M
        )
        if close and len(prev.corners) < MERGE_MAX:
            c = by_id[cue.corner]
            then = c.name or ("left" if c.direction == "L" else "right")
            prev.corners.append(cue.corner)
            prev.text = f"{prev.text} Then {then}."
            if prev.short:
                prev.short = f"{prev.short} Then {then}."
            continue
        out.append(cue)
    return out


def keep_rewritten(old: CuePlan, new: CuePlan) -> None:
    """Text rewritten by the coach or driver survives a rebuild (the corners must still match)."""
    for cue in new.cues:
        before = next((c for c in old.cues if c.corners == cue.corners), None)
        if before is not None and before.source != "template":
            cue.text, cue.source, cue.short = before.text, before.source, before.short


# --- the next session's plan ------------------------------------------------------------------

PLAN_SESSIONS = 2  # a carried focus lasts this many sessions at most


def next_plan_path(root: Path, track_key: str, car_key: str) -> Path:
    return root / "tracks" / track_key / "cues" / f"{car_key}.next.json"


def save_next_plan(
    root: Path,
    track_key: str,
    car_key: str,
    focus: int,
    note: str = "",
    from_session: str | None = None,
    sessions: int = PLAN_SESSIONS,
) -> dict:
    """Start the next sessions here with FOCUS (a corner). The coach re-checks it after two
    pushing laps, so it can't get stuck on something already sorted."""
    plan = {
        "focus": focus,
        "note": note,
        "from_session": from_session,
        "sessions_left": sessions,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    path = next_plan_path(root, track_key, car_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=1) + "\n")
    return plan


def load_next_plan(root: Path, track_key: str, car_key: str) -> dict | None:
    path = next_plan_path(root, track_key, car_key)
    return json.loads(path.read_text()) if path.exists() else None


def plan_for(ws: Workspace, track: str, car: str, ref_id: str | None = None) -> CuePlan:
    """The saved plan for this track and car, built (and saved) if there isn't one or REF_ID asks
    for a different reference lap; a plan in an older format is rebuilt, keeping rewritten cues.
    """
    plan = None if ref_id else load_plan(ws.root, track, car)
    if (
        plan is not None and plan.version < PLAN_VERSION
    ):  # older format: rebuild, keeping rewritten cues
        try:
            plan = build_plan(ws, track, car, plan.ref_lap_id)
        except WorkspaceError:  # its reference lap is gone
            plan = build_plan(ws, track, car)
        save_plan(ws.root, plan)
    if plan is None:
        plan = build_plan(ws, track, car, ref_id)
        path = save_plan(ws.root, plan)
        logger.info(
            "Cue plan for %s / %s from %s: %s", track, car, plan.ref_lap_id, path
        )
    return plan


def use_next_plan(root: Path, track_key: str, car_key: str) -> dict | None:
    """The plan for a session starting now (counted against its sessions; gone when used up)."""
    plan = load_next_plan(root, track_key, car_key)
    if plan is None:
        return None
    path = next_plan_path(root, track_key, car_key)
    plan["sessions_left"] = int(plan.get("sessions_left", 1)) - 1
    if plan["sessions_left"] <= 0:
        path.unlink()
    else:
        path.write_text(json.dumps(plan, indent=1) + "\n")
    return plan
