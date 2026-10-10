"""The live coach: telemetry frames in, spoken coaching out, through one event pipeline.

The coach is a pipeline (`iagent.live.pipeline`) of components (`iagent.live.components`), each
of which only handles events: line crossings and laps, where the car is, pace (pushing or not),
corner exits with their metrics, watched points on track, the corner cues, the focus, what's
known about each corner, the lap summary, the cool-down debrief, the narrator, the rules and,
last, speech. What's said is decided by rules (`iagent.live.builtin_rules` for the coach's own,
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

New behaviour is a new event, component, gate or rule: nothing in this file changes.
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from iagent.analysis.corners import CornerMap
from iagent.live.components import COMPONENTS, CornerResult, CueCaller, Focus, Speaking
from iagent.live.cues import CuePlan
from iagent.live.events import Event
from iagent.live.pipeline import Pipeline
from iagent.live.rules import Rule, RuleEngine
from iagent.live.settings import Settings
from iagent.live.speech import Arbiter
from iagent.telemetry.frames import Frame
from iagent.telemetry.session import SessionInfo

__all__ = ["CornerResult", "LiveCoach", "Settings"]


@dataclass
class Context:
    """What every component may read: the session, the plan, the corner map, the reference lap."""

    session: SessionInfo
    plan: CuePlan
    cmap: CornerMap
    ref_d: np.ndarray
    ref_t: np.ndarray
    arbiter: Arbiter
    own_best: pd.DataFrame | None = None
    carried_focus: int | None = None
    rules: list[Rule] | None = None  # the agent's
    engineer: object | None = None  # iagent.live.engineer.Engineer: the session's conversation
    radio_context: Callable[[], dict] | None = None  # what the engineer is told about the session


class LiveCoach:
    def __init__(self, session: SessionInfo, plan: CuePlan, cmap: CornerMap, ref_grid: pd.DataFrame,
                 arbiter: Arbiter, settings: Settings | None = None, own_best: pd.DataFrame | None = None,
                 carried_focus: int | None = None, rules: list[Rule] | None = None, engineer=None,
                 radio_context: Callable[[], dict] | None = None, components: tuple[type, ...] = ()):
        """COMPONENTS are added to the standard ones (before the rules and speech): an extension
        that only defines events, components and rules plugs in here."""
        self.ctx = Context(session, plan, cmap, ref_grid["LapDist"].to_numpy(dtype=float),
                           ref_grid["lap_time_s"].to_numpy(dtype=float), arbiter, own_best, carried_focus, rules,
                           engineer, radio_context)
        self.settings = settings or Settings()
        self.pipeline = Pipeline(self.ctx, self.settings, [*COMPONENTS, *components, RuleEngine, Speaking])

    # --- driving it ----------------------------------------------------------------------------

    def push(self, frame: Frame) -> None:
        self.pipeline.push(frame)

    def post(self, event: str, **fields) -> None:
        """Hand in an event from any thread (a command, a reply): handled at the next frame."""
        self.pipeline.post(event, **fields)

    def on(self, event: str, fn: Callable[[Event], None]) -> None:
        self.pipeline.on(event, fn)

    def finish(self, now: float) -> None:
        """End of the stream: the session is over (the engineer writes up its notes); let anything
        queued play out (for replays and tests)."""
        self.pipeline.emit("session_end")
        self.pipeline.run()
        end, t = now + 30.0, now
        while self.arbiter.queue and t < end:
            self.arbiter.tick(t)
            t += 0.1
        self.pipeline.run()

    # --- what it knows -------------------------------------------------------------------------

    @property
    def state(self) -> dict:
        return self.pipeline.state

    @property
    def session(self) -> SessionInfo:
        return self.ctx.session

    @property
    def plan(self) -> CuePlan:
        return self.ctx.plan

    @property
    def cmap(self) -> CornerMap:
        return self.ctx.cmap

    @property
    def arbiter(self) -> Arbiter:
        return self.ctx.arbiter

    @property
    def length(self) -> float:
        return self.ctx.plan.length_m

    @property
    def rules(self) -> RuleEngine:
        return self.pipeline.get(RuleEngine)

    @property
    def laps(self) -> int:
        return self.state["lap"]

    @property
    def mode(self) -> str:
        return self.state["mode"]

    @property
    def focus(self) -> int | None:
        return self.state["focus"]

    @property
    def focus_log(self) -> list[dict]:
        return self.state["focus_log"]

    def focus_entry(self) -> dict | None:
        return self.pipeline.get(Focus)._entry()

    def wanted(self, cue) -> bool:
        """Would this cue be said on its next approach (if the driver is pushing)?"""
        caller = self.pipeline.get(CueCaller)
        return caller.wanted(cue) if self.state["pushing"] else bool(self.state["learning"])
