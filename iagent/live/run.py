"""Running the live coach: where frames come from (iRacing's shared memory on the sim PC, or a
recording replayed in real time) and the loop that feeds them to a coach.

The replay is how the live coach is developed and heard away from the sim: the same frames, at
the speed they were recorded (or faster).
"""

import logging
import time
from pathlib import Path
from typing import Callable, Iterator, Sequence

from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import use_next_plan
from iagent.live.rules.store import load_rules, signature
from iagent.live.speech import Voice
from iagent.telemetry.frames import CORE_CHANNELS, Frame
from iagent.telemetry.session import SessionInfo, parse_session_yaml

# The live coach also wants the spotter state (cars alongside).
LIVE_CHANNELS: tuple[str, ...] = tuple(
    c for c in CORE_CHANNELS if c not in ("Lat", "Lon")
) + ("CarLeftRight",)


def paced(
    frames: Iterator[Frame], speed: float = 1.0, start_at: float | None = None
) -> Iterator[Frame]:
    """Yield frames no faster than their session time allows (`speed` × real time).

    `start_at` skips ahead (session seconds) without waiting, e.g. past the garage.
    """
    origin_session = origin_wall = None
    for frame in frames:
        if start_at is not None and frame.session_time < start_at:
            continue
        if origin_session is None:
            origin_session, origin_wall = frame.session_time, time.monotonic()
        wait = (frame.session_time - origin_session) / speed - (
            time.monotonic() - origin_wall
        )
        if wait > 0:
            time.sleep(wait)
        yield frame


class IrsdkSource:
    """iRacing's live telemetry (Windows, sim running). Waits for the sim, then yields a frame
    each time iRacing publishes one (60 Hz), until the sim closes or `stop()` is called.

    Lat/Lon aren't in the live SDK (only in recordings); nothing live depends on them.
    """

    def __init__(self, channels: Sequence[str] = LIVE_CHANNELS, poll_hz: float = 120.0):
        import irsdk

        self._ir = irsdk.IRSDK()
        self._channels = tuple(channels)
        self._interval = 1.0 / poll_hz
        self._session: SessionInfo | None = None
        self._session_update = -1
        self._stopped = False

    def connect(self, timeout_s: float | None = None) -> bool:
        """Wait for iRacing; True once connected."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while not (self._ir.is_initialized and self._ir.is_connected):
            if self._stopped or (deadline is not None and time.monotonic() > deadline):
                return False
            self._ir.startup()
            if not self._ir.is_connected:
                time.sleep(1.0)
        return True

    @property
    def session(self) -> SessionInfo:
        """The session info, re-read from iRacing when it changes."""
        update = self._ir.session_info_update
        if self._session is None or update != self._session_update:
            header = self._ir._header
            raw = bytes(
                self._ir._shared_mem[
                    header.session_info_offset : header.session_info_offset
                    + header.session_info_len
                ]
            )
            self._session = parse_session_yaml(
                raw.rstrip(b"\x00").decode("latin-1"), session_id="live"
            )
            self._session_update = update
        return self._session

    def stop(self) -> None:
        self._stopped = True

    def frames(self) -> Iterator[Frame]:
        """Frames from iRacing as they come (one per sim tick), until it disconnects or `stop` is
        called."""
        last_tick = None
        while not self._stopped:
            if not self._ir.is_connected:
                return
            values: dict[str, float] = {}
            self._ir.freeze_var_buffer_latest()  # a consistent snapshot of one tick
            try:
                tick = self._ir["SessionTick"]
                if tick != last_tick:
                    last_tick = tick
                    for name in self._channels:
                        value = self._ir[name]
                        if value is not None:
                            values[name] = float(value)
            finally:
                self._ir.unfreeze_var_buffer_latest()
            if "SessionTime" in values:
                yield Frame(values["SessionTime"], values)
            time.sleep(self._interval)

    def close(self) -> None:
        self._ir.shutdown()


logger = logging.getLogger("iagent.live")


def start_coach(
    root: Path,
    session: SessionInfo,
    voice: Voice,
    settings: Settings | None = None,
    ref_id: str | None = None,
    engineer=None,
    radio_context=None,
) -> LiveCoach:
    """A coach for a live (or replayed) session: the active rules, a focus planned last session,
    and the cues rendered by the voice up front so none costs time at a brake point."""
    rules = load_rules(root, session.track_key, session.car_key, "active")
    if rules:
        logger.info("Active rules: %s", ", ".join(r.id for r in rules))
    carried = use_next_plan(root, session.track_key, session.car_key)
    if carried:
        logger.info(
            "Starting with the planned focus T%s (%s more session(s))",
            carried["focus"],
            carried["sessions_left"],
        )
    coach = LiveCoach.load(
        root,
        session,
        voice,
        settings,
        rules,
        ref_id,
        carried["focus"] if carried else None,
        engineer,
        radio_context,
    )
    if hasattr(voice, "prepare"):  # cues, and rule lines with fixed text
        fixed = [
            a.template.text
            for r in rules
            for a in r.actions
            if a.key == "say" and not a.template.names
        ]
        logger.info(
            "Rendered %d line(s)",
            voice.prepare([c.text for c in coach.ctx.plan.cues] + fixed),
        )
    return coach


def reload_rules(root: Path, coach: LiveCoach) -> None:
    """Pick up rules activated, changed or deactivated since the coach started."""
    rules = load_rules(
        root, coach.ctx.session.track_key, coach.ctx.session.car_key, "active"
    )
    coach.rules.replace(rules)
    logger.info(
        "Rules reloaded: %s",
        ", ".join(r.id for r in coach.rules.rules) or "none active",
    )


def run(
    root: Path,
    session_of: Callable[[], SessionInfo],
    frames: Iterator[Frame],
    voice: Voice,
    settings: Settings | None = None,
    ref_id: str | None = None,
    on_start: Callable[[LiveCoach], None] | None = None,
    engineer=None,
    radio_context: Callable[[], dict] | None = None,
) -> LiveCoach | None:
    """Coach until the frames run out. A different track or car (a new session) restarts the
    coach with that combination's plan."""
    coach: LiveCoach | None = None
    key = None
    last: Frame | None = None
    rules_sig = None
    for i, frame in enumerate(frames):
        if (
            coach is None or i % 600 == 0
        ):  # check for a new session (and changed rules) every ~10 s
            session = session_of()
            if (session.track_key, session.car_key) != key:
                key = (session.track_key, session.car_key)
                rules_sig = signature(root)
                coach = start_coach(
                    root, session, voice, settings, ref_id, engineer, radio_context
                )
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
