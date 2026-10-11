"""Wire a telemetry source, the cue plan, the active rules and a voice into a running live coach."""

import logging
from pathlib import Path
from typing import Callable, Iterator

from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import use_next_plan
from iagent.live.rulebook import load_rules, signature
from iagent.live.speech import Voice
from iagent.telemetry.frames import Frame
from iagent.telemetry.session import SessionInfo

logger = logging.getLogger("iagent.live")


def start_coach(root: Path, session: SessionInfo, voice: Voice, settings: Settings | None = None,
                ref_id: str | None = None, engineer=None, radio_context=None) -> LiveCoach:
    """A coach for a live (or replayed) session: the active rules, a focus planned last session,
    and the cues rendered by the voice up front so none costs time at a brake point."""
    rules = load_rules(root, session.track_key, session.car_key, "active")
    if rules:
        logger.info("Active rules: %s", ", ".join(r.id for r in rules))
    carried = use_next_plan(root, session.track_key, session.car_key)
    if carried:
        logger.info("Starting with the planned focus T%s (%s more session(s))", carried["focus"], carried["sessions_left"])
    coach = LiveCoach.load(root, session, voice, settings, rules, ref_id, carried["focus"] if carried else None,
                           engineer, radio_context)
    if hasattr(voice, "prepare"):  # cues, and rule lines with fixed text
        fixed = [a.template.text for r in rules for a in r.actions if a.key == "say" and not a.template.names]
        logger.info("Rendered %d line(s)", voice.prepare([c.text for c in coach.ctx.plan.cues] + fixed))
    return coach


def reload_rules(root: Path, coach: LiveCoach) -> None:
    """Pick up rules activated, changed or deactivated since the coach started."""
    rules = load_rules(root, coach.ctx.session.track_key, coach.ctx.session.car_key, "active")
    coach.rules.replace(rules)
    logger.info("Rules reloaded: %s", ", ".join(r.id for r in coach.rules.rules) or "none active")


def run(root: Path, session_of: Callable[[], SessionInfo], frames: Iterator[Frame], voice: Voice,
        settings: Settings | None = None, ref_id: str | None = None,
        on_start: Callable[[LiveCoach], None] | None = None, engineer=None,
        radio_context: Callable[[], dict] | None = None) -> LiveCoach | None:
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
                coach = start_coach(root, session, voice, settings, ref_id, engineer, radio_context)
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
