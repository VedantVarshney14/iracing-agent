"""Wire a telemetry source, the cue plan, the active rules and a voice into a running live coach."""

import logging
from pathlib import Path
from typing import Callable, Iterator

from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import PLAN_VERSION, CuePlan, build_plan, load_plan, save_plan, use_next_plan
from iagent.live.rulebook import load_rules, signature
from iagent.live.speech import Arbiter, Voice
from iagent.telemetry.frames import Frame
from iagent.telemetry.session import SessionInfo
from iagent.workspace import Workspace, WorkspaceError

logger = logging.getLogger("iagent.live")


def plan_for(ws: Workspace, session: SessionInfo, ref_id: str | None = None, rebuild: bool = False) -> CuePlan:
    """The saved plan for this track and car, built (and saved) if there isn't one."""
    plan = None if rebuild or ref_id else load_plan(ws.root, session.track_key, session.car_key)
    if plan is not None and plan.version < PLAN_VERSION:  # older format: rebuild, keeping rewritten cues
        try:
            plan = build_plan(ws, session.track_key, session.car_key, plan.ref_lap_id)
        except WorkspaceError:  # its reference lap is gone
            plan = build_plan(ws, session.track_key, session.car_key)
        save_plan(ws.root, plan)
    if plan is None:
        plan = build_plan(ws, session.track_key, session.car_key, ref_id)
        path = save_plan(ws.root, plan)
        logger.info("Cue plan for %s / %s from %s: %s", session.track_key, session.car_key, plan.ref_lap_id, path)
    return plan


def start_coach(root: Path, session: SessionInfo, voice: Voice, settings: Settings | None = None,
                ref_id: str | None = None, narrator=None, narrator_context=None) -> LiveCoach:
    ws = Workspace(root)
    try:
        plan = plan_for(ws, session, ref_id)
        cmap = ws.corner_map(session.track_key)
        ref_grid = ws.load(plan.ref_lap_id)
        own = own_best(ws, session.track_key, session.car_key)
    finally:
        ws.close()
    if hasattr(voice, "prepare"):
        rendered = voice.prepare(c.text for c in plan.cues)
        logger.info("Rendered %d cue(s)", rendered)
    carried = use_next_plan(root, session.track_key, session.car_key)
    if carried:
        logger.info("Starting with the planned focus T%s (%s more session(s))", carried["focus"], carried["sessions_left"])
    rules = load_rules(root, session.track_key, session.car_key, "active")
    if rules:
        logger.info("Active rules: %s", ", ".join(r.id for r in rules))
    if hasattr(voice, "prepare"):  # cue-like rule lines with fixed text can be rendered now too
        fixed = [a.template.text for r in rules for a in r.actions if a.kind == "say" and not a.template.names]
        if fixed:
            voice.prepare(fixed)
    return LiveCoach(session, plan, cmap, ref_grid, Arbiter(voice), settings, own_best=own,
                     carried_focus=carried["focus"] if carried else None, rules=rules, narrator=narrator,
                     narrator_context=narrator_context)


def reload_rules(root: Path, coach: LiveCoach) -> None:
    """Pick up rules activated, changed or deactivated since the coach started."""
    rules = load_rules(root, coach.session.track_key, coach.session.car_key, "active")
    coach.rules.replace(rules)
    logger.info("Rules reloaded: %s", ", ".join(r.id for r in coach.rules.rules) or "none active")


def own_best(ws: Workspace, track: str, car: str):
    """The driver's fastest valid lap here (the pace that counts as pushing), or None."""
    if not (ws.root / "index.sqlite").exists():
        return None
    laps = [r for r in ws.store().list(track=track, car=car, valid_only=True) if r.lap_time]
    return ws.load(min(laps, key=lambda r: r.lap_time).lap_id) if laps else None


def run(root: Path, session_of: Callable[[], SessionInfo], frames: Iterator[Frame], voice: Voice,
        settings: Settings | None = None, ref_id: str | None = None,
        on_start: Callable[[LiveCoach], None] | None = None, narrator=None,
        narrator_context: Callable[[], dict] | None = None) -> LiveCoach | None:
    """Coach until the frames run out. A different track or car (a new session) restarts the
    coach with that combination's plan."""
    coach: LiveCoach | None = None
    key = None
    last: Frame | None = None
    rules_sig = None
    for i, frame in enumerate(frames):
        if coach is None or i % 600 == 0:  # check for a new session (and changed rules) every ~10 s
            session = session_of()
            if (session.track_key, session.car_key) != key:
                key = (session.track_key, session.car_key)
                rules_sig = signature(root)
                coach = start_coach(root, session, voice, settings, ref_id, narrator, narrator_context)
                if on_start:
                    on_start(coach)
            elif (sig := signature(root)) != rules_sig:
                rules_sig = sig
                reload_rules(root, coach)
        coach.push(frame)
        last = frame
    if coach is not None and last is not None:
        coach.finish(last.session_time)
    return coach
