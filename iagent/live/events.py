"""The live coach's events: everything that happens, as typed dataclasses.

Every event is a subclass of `Event` with its fields declared (and documented) on the class, so
an event's schema is its class. The pipeline delivers them, components act on them
(`iagent.live.pipeline`), rules are written against them by `name`, and `iagent rules vars` lists
them from these classes. A new event is a new class here (or anywhere: subclassing registers it).

Fields are declared with `doc(...)`: a description, and whether it's a corner (rules accept a
corner's name for it) or internal (objects for components only: not logged, not for rules).
"""

from dataclasses import dataclass, field, fields
from typing import Any, Callable, ClassVar

EVENTS: dict[str, type["Event"]] = {}  # name -> class


def doc(text: str, default: Any = None, *, corner: bool = False, internal: bool = False,
        factory: Callable[[], Any] | None = None) -> Any:
    """A documented event (or state) field."""
    meta = {"doc": text, "corner": corner, "internal": internal}
    if factory is not None:
        return field(default_factory=factory, metadata=meta)
    return field(default=default, metadata=meta)


@dataclass(kw_only=True)
class Event:
    """Base of every event. `name` is how rules and the session log refer to it; `log` puts it in
    the session log; `judged` marks events about driving, which rules with `pushing_only` ignore
    while the driver isn't pushing."""

    name: ClassVar[str] = ""
    log: ClassVar[bool] = False
    judged: ClassVar[bool] = False
    at: float = field(default=0.0, metadata={"doc": "session time (s)", "internal": True})

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("name"):
            if cls.name in EVENTS and EVENTS[cls.name].__qualname__ != cls.__qualname__:
                raise ValueError(f"Two events are called {cls.name!r}.")
            EVENTS[cls.name] = cls

    @classmethod
    def field_docs(cls) -> dict[str, str]:
        return {f.name: f.metadata.get("doc", "") for f in fields(cls) if not f.metadata.get("internal")}

    @classmethod
    def corner_fields(cls) -> set[str]:
        return {f.name for f in fields(cls) if f.metadata.get("corner")}

    @classmethod
    def variable_docs(cls) -> dict[str, str]:
        """What a rule on this event can read (beyond the shared state)."""
        return cls.field_docs()

    def public(self) -> dict[str, Any]:
        """The fields worth logging: everything but internal objects."""
        return {f.name: getattr(self, f.name) for f in fields(self) if not f.metadata.get("internal")}

    def variables(self) -> dict[str, Any]:
        """What a rule on this event sees (beyond the shared state)."""
        return self.public()


def _named(values: dict) -> dict:
    """Rules call a corner's name `name` (`corner_name` on the class: `name` is the event's)."""
    out = {k: v for k, v in values.items() if k != "corner_name"}
    if "corner_name" in values:
        out["name"] = values["corner_name"]
    return out


# --- the pipeline itself ------------------------------------------------------------------------

@dataclass(kw_only=True)
class Frame(Event):
    """Every telemetry frame (60 Hz). Rules on it are edge-triggered conditions."""
    name: ClassVar[str] = "frame"
    judged: ClassVar[bool] = True
    frame: Any = doc("the telemetry frame", internal=True)


@dataclass(kw_only=True)
class FrameDone(Event):
    """After everything a frame caused (speech decides what to say then)."""
    name: ClassVar[str] = "frame_done"


@dataclass(kw_only=True)
class SessionStart(Event):
    """The coach's first frame."""
    name: ClassVar[str] = "session_start"


@dataclass(kw_only=True)
class SessionEnd(Event):
    """The session is over (the frames stopped)."""
    name: ClassVar[str] = "session_end"


@dataclass(kw_only=True)
class Clock(Event):
    """Once a second."""
    name: ClassVar[str] = "clock"
    since_start_s: int = doc("whole seconds since the coach started", 0)


# --- the car on track ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class Pit(Event):
    """Pit road entry or exit."""
    name: ClassVar[str] = "pit"
    pit: str = doc('"entry" or "exit"', "")


@dataclass(kw_only=True)
class PaceChanged(Event):
    """The coach's judgement of the driver's pace changed."""
    name: ClassVar[str] = "pace"
    log: ClassVar[bool] = True
    mode: str = doc('"pushing" or "tranquille"', "")
    lap_dist: float | None = doc("where (m)")


@dataclass(kw_only=True)
class Slowing(Event):
    """A slow stretch has lasted `narrate_after_s` (probably not just a moment)."""
    name: ClassVar[str] = "slowing"
    stretch: int = doc("which slow stretch (counts up)", 0)


@dataclass(kw_only=True)
class CoolDown(Event):
    """A slow stretch has lasted `debrief_after_s`: a cool-down, time to talk."""
    name: ClassVar[str] = "cool_down"
    stretch: int = doc("which slow stretch", 0)


@dataclass(kw_only=True)
class Crossing(Event):
    """The car crossed the start/finish line (every lap, counted or not). Components add what they
    know about the lap just finished; a counted one becomes a `Lap`."""
    name: ClassVar[str] = "crossing"
    complete: bool = doc("the lap ran line to line", False)
    clean: bool = doc("it started at the line and stayed off pit road", False)
    pace: str = doc('"pushing", "moment" or "tranquille"', "pushing")
    pushing: bool = doc("the lap counts as pushing (not tranquille)", True)
    pushing_share: float = doc("share of the lap pushing (0-1)", 0.0)
    slow: list = doc("[from_m, to_m] stretches not pushing", factory=list)
    moment_at: int | None = doc("corner where a moment started, or none", corner=True)
    corners: list = doc("[{corner, delta_s}] for the corners driven at pace", factory=list)
    segment: Any = doc("the lap's samples (iagent.laps.segment.Lap)", internal=True)
    results: list = doc("the corners at pace (CornerResult)", internal=True, factory=list)


@dataclass(kw_only=True)
class Lap(Crossing):
    """A counted lap, at the line: complete, from the line, no pit road."""
    name: ClassVar[str] = "lap"
    log: ClassVar[bool] = True
    judged: ClassVar[bool] = True
    lap: int = doc("laps counted so far, this one included", 0)
    lap_time: float | None = doc("s")
    gap_s: float | None = doc("vs the reference (+ slower)")
    new_best: bool = doc("a new best lap (pushing)", False)
    best_gap_s: float | None = doc("vs the best lap before this one (+ slower)")
    worst_corner: int | None = doc("corner that lost the most", corner=True)
    worst_name: str | None = doc("its name")
    worst_delta_s: float | None = doc("how much it lost")
    focus: int | None = doc("the focus cue after this lap", corner=True)
    focus_news: str | None = doc("what changed about the focus, to say")
    summary_text: str | None = doc("the lap summary as said, or none")


# --- corners ------------------------------------------------------------------------------------

METRICS = {
    "brake_m": "where braking began (m), none if not braked",
    "brake_peak": "peak brake pressure on the approach (0-1)",
    "min_throttle": "lowest throttle on the approach (0-1): a lift if well below 1",
    "entry_speed_kph": "fastest point before the apex",
    "min_speed_kph": "slowest point in the corner",
    "min_speed_m": "where the slowest point was (m)",
    "full_throttle_m": "back on full throttle (m), none if never in the segment",
    "exit_speed_kph": "speed at the corner's exit",
    "min_gear": "lowest gear",
    "off_track_m": "metres off track in the corner's segment",
    "time_s": "time through the corner's segment",
}
DIFFS = {  # name: (metric, description); this lap's minus the reference's
    "brake_diff_m": ("brake_m", "brake_m - ref_brake_m: > 0 braked later"),
    "min_speed_diff_kph": ("min_speed_kph", "min_speed_kph - ref_min_speed_kph: > 0 carried more speed"),
    "throttle_diff_m": ("full_throttle_m", "full_throttle_m - ref_full_throttle_m: > 0 on full throttle later"),
    "exit_speed_diff_kph": ("exit_speed_kph", "exit_speed_kph - ref_exit_speed_kph"),
}


@dataclass(kw_only=True)
class CornerExit(Event):
    """Just past a corner's exit, with its metrics against the reference lap. Rules see each metric
    by name (`brake_m`), the reference's (`ref_brake_m`) and the differences (`brake_diff_m`)."""
    name: ClassVar[str] = "corner_exit"
    judged: ClassVar[bool] = True
    corner: int = doc("the corner", 0, corner=True)
    corner_name: str = doc("its name, or 'Turn N'", "")
    delta_s: float = doc("time lost (+) or gained (-) vs the reference", 0.0)
    at_pace: bool = doc("driven pushing on a counted lap", False)
    pushing: bool = doc("same as at_pace (what pushing_only looks at)", False)
    cause: str | None = doc("the clearest cause of the loss (see phrasing.CAUSES), or none")
    amount: float | None = doc("how much, for the cause")
    advice: str | None = doc("what to say about it, or none")
    hint: str | None = doc("for the corner's cue next lap, or none")
    longer: str | None = doc("the advice with the why and the how")
    struggling: bool = doc("it went badly (lost time, or off)", False)
    in_focus: bool = doc("it's (part of) the focus", False)
    feedback: str | None = doc("what to say about it now (aware of last time), or none")
    feedback_long: str | None = doc("the same with the why and the how")
    metrics: dict = doc("this lap's corner metrics", internal=True, factory=dict)
    ref: dict = doc("the reference lap's", internal=True, factory=dict)
    result: Any = doc("the CornerResult", internal=True)

    @classmethod
    def variable_docs(cls) -> dict[str, str]:
        return {**_named(cls.field_docs()), **METRICS, **{f"ref_{k}": f"the reference lap's {k}" for k in METRICS},
                **{k: v[1] for k, v in DIFFS.items()}}

    def variables(self) -> dict[str, Any]:
        out = _named(self.public())
        out.update({k: self.metrics.get(k) for k in METRICS})
        out.update({f"ref_{k}": self.ref.get(k) for k in METRICS})
        for key, (metric, _) in DIFFS.items():
            a, b = self.metrics.get(metric), self.ref.get(metric)
            out[key] = None if a is None or b is None else round(a - b, 1)
        return out


@dataclass(kw_only=True)
class Advice(Event):
    """Advice for a corner: said after it, or as a hint in its cue next lap."""
    name: ClassVar[str] = "advice"
    log: ClassVar[bool] = True
    corner: int = doc("the corner", 0, corner=True)
    lap: int = doc("the lap it was about", 0)
    delta_s: float = doc("time lost", 0.0)
    advice: str | None = doc("said after the corner, or none")
    hint: str | None = doc("for its cue next lap, or none")


# --- points on track ----------------------------------------------------------------------------

@dataclass(kw_only=True)
class Watch(Event):
    """Ask for an `Approach` before the car reaches a point. `speak` says how long what will be said
    takes (None: nothing to say right now)."""
    name: ClassVar[str] = "watch"
    id: str = doc("the watch's name", "")
    target_m: float = doc("the point (m from the line)", 0.0)
    lead_s: float = doc("finish this long before it", 0.0)
    tags: dict = doc("fields set on its approach events", factory=dict)
    speak: Callable[[], float | None] = doc("seconds of speech planned there", internal=True)


@dataclass(kw_only=True)
class Unwatch(Event):
    """Stop watching a point."""
    name: ClassVar[str] = "unwatch"
    id: str = doc("the watch's name", "")


@dataclass(kw_only=True)
class Approach(Event):
    """The car is about to reach a watched point: a line started now finishes `lead_s` before it."""
    name: ClassVar[str] = "approach"
    judged: ClassVar[bool] = True
    watch: str = doc("the watch's name", "")
    source: str = doc("who asked (cue, rule)", "")
    target_m: float = doc("the point (m)", 0.0)
    to_target_m: float = doc("how far it is (m)", 0.0)
    eta_s: float = doc("seconds to it at the current speed", 0.0)
    lead_s: float = doc("finish this long before it", 0.0)
    expires_at: float | None = doc("session time after which a line can no longer finish before it")
    corner: int | None = doc("the corner, if the point is one", corner=True)
    corners: list = doc("the cue's corners", factory=list)
    corner_name: str | None = doc("the corner's name")
    wanted: bool = doc("a cue worth saying now", False)
    text: str | None = doc("the cue as it would be said")
    full: bool = doc("said in full (not the short form)", False)

    @classmethod
    def variable_docs(cls) -> dict[str, str]:
        return _named(cls.field_docs())

    def variables(self) -> dict[str, Any]:
        return _named(self.public())


# --- the focus ----------------------------------------------------------------------------------

@dataclass(kw_only=True)
class FocusChanged(Event):
    """The focus changed."""
    name: ClassVar[str] = "focus"
    log: ClassVar[bool] = True
    focus: dict | None = doc("the new focus: {cue, corners, label, set_lap, done_lap, manual}, or none")
    by: str = doc('"coach" or "driver"', "coach")


# --- the cool-down debrief ----------------------------------------------------------------------

@dataclass(kw_only=True)
class DebriefGiven(Event):
    """The debrief was given."""
    name: ClassVar[str] = "debrief"
    log: ClassVar[bool] = True
    by: str = doc('"coach" (the engineer\'s words) or "template"', "template")
    text: str = doc("all of it", "")


@dataclass(kw_only=True)
class DebriefLine(Event):
    """A piece of the debrief, to say."""
    name: ClassVar[str] = "debrief_line"
    text: str = doc("the words", "")
    index: int = doc("which piece", 0)


# --- speech -------------------------------------------------------------------------------------

@dataclass(kw_only=True)
class Line(Event):
    """A line was said, cut off by something more urgent, or dropped (and why)."""
    name: ClassVar[str] = "line"
    log: ClassVar[bool] = True
    status: str = doc('"said", "cut" or "dropped"', "")
    kind: str = doc("what it was (approach, feedback, summary, ...)", "")
    text: str = doc("the words", "")
    corner: int | None = doc("the corner it was about", corner=True)
    note: str | None = doc("why it was dropped")
    rule: str | None = doc("the rule that said it")


@dataclass(kw_only=True)
class RuleFired(Event):
    """A rule fired, or a limit held it back."""
    name: ClassVar[str] = "rule"
    log: ClassVar[bool] = True
    rule: str = doc("the rule", "")
    result: str = doc('"fired" or "limited"', "")
    why: str | None = doc("the limit that held it")
    trigger: str = doc("the event it fired on", "")
    values: dict = doc("the values it looked at", factory=dict)
    actions: list = doc("what it did", factory=list)
    lap: int = doc("laps counted", 0)
    lap_no: int = doc("line crossings", 0)
    lap_dist: float | None = doc("where (m)")


# --- from the driver and the browser (posted from other threads) -------------------------------

@dataclass(kw_only=True)
class Say(Event):
    """Say something (an answer to the driver)."""
    name: ClassVar[str] = "say"
    text: str = doc("the words", "")
    kind: str = doc("what it is (answer, ...)", "answer")


@dataclass(kw_only=True)
class SetFocus(Event):
    """The driver (or the coach) picks the focus."""
    name: ClassVar[str] = "set_focus"
    corner: int | None = doc("a corner of the cue, or none", corner=True)


@dataclass(kw_only=True)
class Error(Event):
    """Something asked of the coach couldn't be done."""
    name: ClassVar[str] = "error"
    log: ClassVar[bool] = True
    message: str = doc("why", "")


# --- the engineer -------------------------------------------------------------------------------

@dataclass(kw_only=True)
class Narrate(Event):
    """Ask the engineer (the model) for its words; they come back as the `reply` event."""
    name: ClassVar[str] = "narrate"
    kind: str = doc("what it's for: briefing, question, wake, debrief, wrap_up", "")
    reply: str = doc("the event the words come back as", "coach_words")
    stretch: int | None = doc("passed back with the reply")
    rule: str | None = doc("passed back with the reply")
    min_gap_s: float = doc("skip it if one of this kind was asked for less than this long ago", 0.0)
    payload: dict = doc("what the prompt is made from (facts, the question, ...)", internal=True, factory=dict)


@dataclass(kw_only=True)
class NarrationAsked(Event):
    """The engineer is working on its words."""
    name: ClassVar[str] = "narration_asked"
    kind: str = doc("what for", "")
    reply: str = doc("the event they'll come back as", "")
    stretch: int | None = doc("as asked")


@dataclass(kw_only=True)
class Reply(Event):
    """Base of the engineer's replies (posted back from its thread)."""
    text: str | None = doc("the words, or none")
    asked_at: float | None = doc("when they were asked for")
    stretch: int | None = doc("as asked")
    rule: str | None = doc("as asked")
    kind: str | None = doc("what they answer")


@dataclass(kw_only=True)
class CoachWords(Reply):
    """The engineer's words to say: a radio check, a reply to a wake-up or to a question."""
    name: ClassVar[str] = "coach_words"
    log: ClassVar[bool] = True


@dataclass(kw_only=True)
class DebriefWords(Reply):
    """The engineer's words for a debrief arrived."""
    name: ClassVar[str] = "debrief_words"
    log: ClassVar[bool] = True
    status: str = doc('"used", "too late" or "none"', "none")


@dataclass(kw_only=True)
class NotesWritten(Reply):
    """The engineer wrote up its notes after the session."""
    name: ClassVar[str] = "notes_written"
    log: ClassVar[bool] = True


@dataclass(kw_only=True)
class Wake(Event):
    """A rule woke the engineer."""
    name: ClassVar[str] = "wake"
    log: ClassVar[bool] = True
    rule: str = doc("the rule", "")
    message: str = doc("its message", "")
    values: dict = doc("what it saw", factory=dict)
    description: str = doc("the rule's description", "")
