"""Events and shared state: the vocabulary of the live pipeline.

Everything the live coach knows or does is an event: a frame of telemetry, a line crossing, a
corner exit with its metrics, a counted lap, a change of pace, the car approaching a point, a
cool-down, a line said or dropped, a reply from the narrator, a command from the driver. Each is
*defined* here (or next to the component that produces it) with its fields, documented, so
rules can use it and `iagent rules vars` can list it. Nothing else needs to know about it.

Shared state works the same way: values components maintain (the pace mode, the focus, the
corners the driver struggles with) are defined with a description, and any rule can read them.

Defining a new event:

    CORNER_EXIT = define_event("corner_exit", "Just past a corner's exit, with its metrics.",
                               {"corner": ("corner", "the corner"), "delta_s": "time lost (+)"},
                               judged=True)

Field descriptions are a string, or (type, description) for fields with a type rules can filter
by name (`corner`: a number, "T9" or a corner's name).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldDef:
    name: str
    doc: str
    type: str = "value"  # "value", or "corner" (filters accept corner names)
    internal: bool = False  # objects for components only: not logged, not for rules


@dataclass(frozen=True)
class EventDef:
    name: str
    doc: str
    fields: dict[str, FieldDef]
    log: bool = False  # written to the session log (the review page reads it)
    judged: bool = False  # about driving: rules with `pushing_only` ignore it while not pushing


EVENTS: dict[str, EventDef] = {}
STATE: dict[str, str] = {}  # shared state name -> description


def define_event(name: str, doc: str, fields: dict[str, Any] | None = None, *, log: bool = False,
                 judged: bool = False, internal: tuple[str, ...] = ()) -> EventDef:
    defs = {}
    for key, spec in (fields or {}).items():
        kind, text = spec if isinstance(spec, tuple) else ("value", spec)
        defs[key] = FieldDef(key, text, kind)
    for key in internal:
        defs[key] = FieldDef(key, "(internal)", internal=True)
    if name in EVENTS:
        raise ValueError(f"Event {name!r} is defined twice; add fields with define_fields.")
    EVENTS[name] = EventDef(name, doc, defs, log, judged)
    return EVENTS[name]


def define_fields(event: str, fields: dict[str, Any] | None = None, internal: tuple[str, ...] = ()) -> None:
    """Fields a component adds to someone else's event (with `enrich_<event>`), defined next to
    the component that adds them."""
    defn = EVENTS[event]
    extra = dict(defn.fields)
    for key, spec in (fields or {}).items():
        kind, text = spec if isinstance(spec, tuple) else ("value", spec)
        extra[key] = FieldDef(key, text, kind)
    for key in internal:
        extra[key] = FieldDef(key, "(internal)", internal=True)
    EVENTS[event] = EventDef(defn.name, defn.doc, extra, defn.log, defn.judged)


def define_state(**names: str) -> None:
    """Shared state values and what they mean."""
    STATE.update(names)


@dataclass
class Event:
    type: str
    at: float  # session time
    fields: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.fields[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    def __setitem__(self, key: str, value: Any) -> None:
        self.fields[key] = value

    def public(self) -> dict:
        """The fields worth logging and showing rules: everything but internal objects."""
        defn = EVENTS[self.type]
        return {k: v for k, v in self.fields.items() if not (k in defn.fields and defn.fields[k].internal)}


# Events of the pipeline itself.
define_event("frame", "Every telemetry frame (60 Hz). Rules on it are edge-triggered conditions.",
             internal=("frame",), judged=True)
define_event("frame_done", "After everything a frame caused (speech decides what to say then).")
define_event("session_start", "The coach's first frame.")
define_event("clock", "Once a second.", {"since_start_s": "whole seconds since the coach started"})
define_state(
    time="session time (s)",
    since_start_s="seconds since the coach started",
    lap_dist="distance from the line (m)",
    speed_kph="current speed (km/h)",
    track="track key", car="car key",
)
