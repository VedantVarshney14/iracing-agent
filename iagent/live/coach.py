"""The live coach: telemetry frames in, spoken coaching out, through one event pipeline.

The coach is a pipeline (`iagent.live.pipeline`) of event handlers (`iagent.live.handlers`), each
of which only handles events: line crossings and laps, where the car is, pace (pushing or not),
corner exits with their metrics, watched points on track, the corner cues, the focus, what's
known about each corner, the lap summary, the cool-down debrief, the link to the engineer, the rules and,
last, speech. What's said is decided by rules (`iagent.live.rules.builtin` for the coach's own,
plus the agent's), and when it's said by the speech arbiter and its gates.

In short, per frame and with no model involved:

- **Corner cues** on the approach, timed to finish at the reference brake point at the current
  speed; every corner on the learning laps, then the focus and the corners that went badly. Full
  until heard, then short.
- **Feedback** after a corner that cost time, on the next straight, aware of last time ("again",
  "better"), with a hint in the corner's cue next lap. The longer version when there's time.
- **One focus at a time**, picked from the last laps at pace; a **lap summary** at the line.
- **Only while pushing**: out laps, cool-downs and moments are neither cued nor judged. A
  cool-down gets a **debrief**, in the coach's own words (`claude -p`) when they come in time.
- Nothing on pit road or off the racing surface, nothing new with a car alongside, and with
  CrewChief running, lap times are left to it.

New behaviour is a new event, event handler, gate or rule: nothing in this file changes.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from iagent.analysis.corners import CornerMap
from iagent.live.cues import CuePlan, plan_for
from iagent.live.events import Event, SessionEnd
from iagent.live.handlers import HANDLERS, CornerResult, CueCaller, Speaking
from iagent.live.pipeline import EventHandler, Pipeline
from iagent.live.rules import Rule, RuleEngine
from iagent.live.settings import Settings
from iagent.live.speech import Arbiter, Voice
from iagent.live.state import CoachState
from iagent.telemetry.frames import Frame
from iagent.telemetry.session import SessionInfo
from iagent.workspace import Workspace

__all__ = ["CornerResult", "LiveCoach", "Settings"]


@dataclass
class Context:
    """What every event handler may read: the session, the cue plan, the corner map, the reference
    lap, the voice, and the engineer if there is one."""

    session: SessionInfo
    plan: CuePlan
    cmap: CornerMap
    ref_d: np.ndarray  # the reference lap: distance (m) ...
    ref_t: np.ndarray  # ... and elapsed time (s) at each point
    arbiter: Arbiter
    own_best: pd.DataFrame | None = None  # the driver's best lap here: the pace that counts as pushing
    carried_focus: int | None = None  # a focus planned in an earlier session
    rules: list[Rule] | None = None  # the agent's
    engineer: object | None = None  # iagent.live.engineer.Engineer: the session's conversation
    radio_context: Callable[[], dict] | None = None  # what the engineer is told about the session


class LiveCoach:
    """The pipeline with the coach's event handlers, the rules and speech. `state` is what it
    knows, `ctx` what it was given, `rules` its rule engine."""

    def __init__(self, session: SessionInfo, plan: CuePlan, cmap: CornerMap, ref_grid: pd.DataFrame,
                 arbiter: Arbiter, settings: Settings | None = None, own_best: pd.DataFrame | None = None,
                 carried_focus: int | None = None, rules: list[Rule] | None = None, engineer=None,
                 radio_context: Callable[[], dict] | None = None, handlers: tuple[type[EventHandler], ...] = ()):
        """`handlers` are added to the standard ones (before the rules and speech): an extension
        that only defines events, event handlers and rules plugs in here."""
        self.ctx = Context(session, plan, cmap, ref_grid["LapDist"].to_numpy(dtype=float),
                           ref_grid["lap_time_s"].to_numpy(dtype=float), arbiter, own_best, carried_focus, rules,
                           engineer, radio_context)
        self.settings = settings or Settings()
        self.pipeline = Pipeline(self.ctx, self.settings, [*HANDLERS, *handlers, RuleEngine, Speaking])

    @classmethod
    def load(cls, root: Path, session: SessionInfo, voice: Voice, settings: Settings | None = None,
             rules: list[Rule] | None = None, ref_id: str | None = None, carried_focus: int | None = None,
             engineer=None, radio_context: Callable[[], dict] | None = None,
             exclude_session: str | None = None) -> "LiveCoach":
        """A coach for SESSION from the workspace at ROOT: its cue plan (built on first use), corner
        map, reference lap and the driver's best lap (leaving out EXCLUDE_SESSION, for backtests)."""
        track, car = session.track_key, session.car_key
        ws = Workspace(root)
        try:
            plan = plan_for(ws, track, car, ref_id)
            cmap = ws.corner_map(track)
            ref_grid = ws.load(plan.ref_lap_id)
            own_best = ws.best_lap(track, car, exclude_session)
        finally:
            ws.close()
        return cls(session, plan, cmap, ref_grid, Arbiter(voice), settings, own_best=own_best,
                   carried_focus=carried_focus, rules=rules, engineer=engineer, radio_context=radio_context)

    @property
    def state(self) -> CoachState:
        return self.pipeline.state

    @property
    def rules(self) -> RuleEngine:
        return self.pipeline.get(RuleEngine)

    def wanted(self, cue) -> bool:
        """Would this cue be said on its next approach (if the driver is pushing)?"""
        return self.pipeline.get(CueCaller).wanted(cue) if self.state.pushing else self.state.learning

    # --- driving it ----------------------------------------------------------------------------

    def push(self, frame: Frame) -> None:
        """One telemetry frame through the pipeline."""
        self.pipeline.push(frame)

    def post(self, event: Event) -> None:
        """Hand in an event from any thread (a command, a reply): handled at the next frame."""
        self.pipeline.post(event)

    def on(self, event: type[Event] | str, fn: Callable[[Event], None]) -> None:
        """Call FN with every EVENT (a class or a name; "*" for all), after the handlers."""
        self.pipeline.on(event, fn)

    def finish(self, now: float) -> None:
        """End of the stream: the session is over (the engineer writes up its notes); let anything
        queued play out (for replays and tests)."""
        self.pipeline.emit(SessionEnd())
        self.pipeline.run()
        arbiter, end, t = self.ctx.arbiter, now + 30.0, now
        while arbiter.queue and t < end:
            arbiter.tick(t)
            t += 0.1
        self.pipeline.run()
