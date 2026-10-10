"""The rule format: a JSON Schema, generated from the events, that rules are validated against.

    {
      "id": "bus-stop-early-brake",
      "track": "spa-2024-up",
      "description": "Brakes too early for the Bus Stop: say so on the straight after.",
      "when": {"event": "corner_exit", "corner": 18},
      "if": "brake_diff_m < -10",
      "actions": [{"say": "{name}: braked {round5(-brake_diff_m)} metres early.", "priority": "feedback"}],
      "limits": {"cooldown_laps": 1}
    }

Rules are written by the agent with a tool call (`iagent rules add`), against the schema
(`iagent rules schema`), so nothing in them is guessed at: no short forms, no corner names to
parse. Corners are the corner map's numbers (`iagent corners list --json`); `when` names an event
(`iagent.live.events`) and filters on its fields, each typed as the event declares it (a value,
or a list of values); `where` adds a condition to the trigger. `approach` takes `at`, a point on
track, `{"corner": 9, "point": "apex"}` or `{"metres": 1234}`, with `offset_m` and `lead_s`;
`frame` takes `edge`, a condition, edge-triggered, with `for_s` and `rearm_s`.
"""

import hashlib
import json
import types
import typing
from dataclasses import dataclass, field, fields
from typing import Any, Callable

import jsonschema

from iagent.analysis.corners import Corner, CornerMap
from iagent.live.events import EVENTS, Approach
from iagent.live.expr import Expr
from iagent.live.rules.actions import PRIORITIES, Action, RuleError, expression, parse_action
from iagent.live.settings import Settings
from iagent.live.state import CoachState

POINTS = ("brake", "entry", "apex", "exit")
STATUSES = ("draft", "active", "archived")
GROUPS = ("agent", "coach")
LIMITS = ("cooldown_s", "cooldown_laps", "max_per_lap", "max_per_session", "once")

# Channels the live coach reads (`iagent.live.sources.LIVE_CHANNELS`), by their iRacing names.
CHANNELS = {
    "Speed": "m/s", "Throttle": "0-1", "Brake": "0-1", "Clutch": "0-1", "Gear": "gear (0 neutral, -1 reverse)",
    "RPM": "engine rpm", "SteeringWheelAngle": "rad", "LatAccel": "m/s²", "LongAccel": "m/s²",
    "VertAccel": "m/s²", "LapDist": "m from the line", "LapDistPct": "0-1 round the lap",
    "OnPitRoad": "1 on pit road", "IsOnTrack": "1 in the car on track",
    "PlayerTrackSurface": "0 off track, 1 pit stall, 2 approaching pits, 3 on track",
    "CarLeftRight": "spotter: 1 clear, 2+ a car alongside", "Lap": "iRacing's lap counter",
    "SessionTime": "s",
}

_NUMBER = {"type": "number"}
_POINT = {"oneOf": [
    {"type": "object", "additionalProperties": False, "required": ["corner"], "description": "a corner and a point in it",
     "properties": {"corner": {"type": "integer", "description": "the corner map's number"},
                    "point": {"enum": list(POINTS), "description": "brake (default): the reference brake point"}}},
    {"type": "object", "additionalProperties": False, "required": ["metres"],
     "properties": {"metres": {"type": "number", "description": "from the line"}}},
]}
# What `when` takes beyond the event's own fields, per event.
EXTRAS: dict[str, dict] = {
    "approach": {"at": {**_POINT, "description": "the point on track"},
                 "offset_m": {**_NUMBER, "description": "move the point (m, + later)"},
                 "lead_s": {**_NUMBER, "description": "finish speaking this long before it"}},
    "frame": {"edge": {"type": "string", "description": "a condition: fires when it becomes true"},
              "for_s": {**_NUMBER, "description": "and has stayed true this long"},
              "rearm_s": {**_NUMBER, "description": "re-armed once false this long"}},
}


def _json_type(tp: Any) -> str | None:
    """The JSON type of an event field, if it's a value rules can filter on."""
    if isinstance(tp, types.UnionType) or typing.get_origin(tp) is typing.Union:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        tp = args[0] if len(args) == 1 else None
    return {bool: "boolean", int: "integer", float: "number", str: "string"}.get(tp)


def when_schema(event: str) -> dict:
    """The `when` of a rule on EVENT: its filterable fields, typed, and its extras."""
    cls = EVENTS[event]
    props: dict[str, Any] = {"event": {"const": event, "description": (cls.__doc__ or "").strip()},
                             "where": {"type": "string", "description": "a condition the event must also meet"}}
    for f in fields(cls):
        t = _json_type(f.type)
        if t is None or f.metadata.get("internal") or f.name == "at":
            continue
        one = {"type": t}
        key = "name" if f.name == "corner_name" else f.name
        props[key] = {"anyOf": [one, {"type": "array", "items": one, "minItems": 1}],
                      "description": f.metadata.get("doc", "")}
    props.update(EXTRAS.get(event, {}))
    return {"type": "object", "additionalProperties": False, "required": ["event"], "properties": props}


def _action_schemas() -> list[dict]:
    text = {"type": "string"}
    return [
        {"type": "object", "additionalProperties": False, "required": ["say"], "properties": {
            "say": {**text, "description": "what to say: text with {expression} holes"},
            "long": {**text, "description": "a fuller version, said when the driver isn't pushing"},
            "priority": {"enum": list(PRIORITIES)}, "kind": {**text, "description": "the line's kind (default: rule)"},
            "expires_s": {"type": ["number", "string"], "description": "seconds (or an expression) it may wait"}}},
        {"type": "object", "additionalProperties": False, "required": ["wake"],
         "properties": {"wake": {**text, "description": "a message for the engineer"}}},
        {"type": "object", "additionalProperties": False, "required": ["log"],
         "properties": {"log": {**text, "description": "noted in the session log"}}},
    ]


def rule_schema() -> dict:
    """The JSON Schema of a rule (`iagent rules schema`)."""
    flags = [f.name for f in fields(Settings) if f.type is bool]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "iagent live coach rule",
        "type": "object", "additionalProperties": False, "required": ["id", "when", "actions"],
        "properties": {
            "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$"},
            "track": {"type": ["string", "null"], "description": "track key; none: every track"},
            "car": {"type": ["string", "null"], "description": "car key; none: every car"},
            "description": {"type": "string", "description": "why the rule exists"},
            "when": {"oneOf": [when_schema(name) for name in sorted(EVENTS)]},
            "if": {"type": "string", "description": "a condition (iagent.live.expr) over the event and the state"},
            "actions": {"type": "array", "minItems": 1, "items": {"oneOf": _action_schemas()}},
            "limits": {"type": "object", "additionalProperties": False, "properties": {
                **{k: {"type": "number", "minimum": 0} for k in LIMITS if k != "once"}, "once": {"type": "boolean"}}},
            "in_a_row": {"type": "integer", "minimum": 1},
            "pushing_only": {"type": "boolean"},
            "group": {"enum": list(GROUPS)},
            "enabled_by": {"enum": flags, "description": "a setting that switches the rule off"},
            "status": {"enum": list(STATUSES)},
            "meta": {"type": "object"},
        },
    }


def _validate(raw: dict) -> None:
    """Against the schema, with the event's own `when` schema (for errors that say what's wrong)."""
    rule_id = raw.get("id", "the rule") if isinstance(raw, dict) else "the rule"
    when = raw.get("when") if isinstance(raw, dict) else None
    event = when.get("event") if isinstance(when, dict) else None
    if isinstance(when, dict) and event not in EVENTS:
        raise RuleError(f"{rule_id}: when.event is one of {', '.join(sorted(EVENTS))}.")
    schema = rule_schema()
    if event in EVENTS:
        schema["properties"]["when"] = when_schema(event)
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(raw), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        where = ".".join(str(p) for p in e.absolute_path) or "the rule"
        raise RuleError(f"{rule_id}: {where}: {e.message} (`iagent rules schema` has the format).")


def variables(event: str) -> set[str]:
    """Every name a rule on EVENT can use: the shared state, the live channels, the event's own."""
    return set(CoachState.docs()) | set(CHANNELS) | set(EVENTS[event].variable_docs())


@dataclass
class Rule:
    id: str
    when: dict
    actions: list[Action]
    track: str | None = None
    car: str | None = None
    description: str = ""
    condition: Expr | None = None
    where: Expr | None = None
    edge: Expr | None = None
    limits: dict = field(default_factory=dict)
    in_a_row: int = 1
    pushing_only: bool = True
    group: str = "agent"  # "coach" for the built-in rules (see the engine's groups)
    enabled_by: str | None = None  # a Settings flag that switches it off
    status: str = "draft"
    meta: dict = field(default_factory=dict)  # created_by, created_at, backtest, ...

    @property
    def trigger(self) -> str:
        return self.when["event"]

    @classmethod
    def from_dict(cls, raw: dict) -> "Rule":
        _validate(raw)
        when, rule_id = raw["when"], raw["id"]
        event = when["event"]
        names = variables(event)
        if event == Approach.name and not ({"at", "watch", "source"} & set(when)):
            raise RuleError(f"{rule_id}: an `approach` rule needs `at` (a point), or a `source` or `watch` filter.")
        cond = raw.get("if")
        return cls(
            id=rule_id, when=dict(when), actions=[parse_action(rule_id, a, event, names) for a in raw["actions"]],
            track=raw.get("track") or None, car=raw.get("car") or None, description=raw.get("description", ""),
            condition=expression(rule_id, cond, names) if cond else None,
            where=expression(rule_id, when["where"], names) if when.get("where") else None,
            edge=expression(rule_id, when["edge"], names) if when.get("edge") else None,
            limits=dict(raw.get("limits") or {}), in_a_row=raw.get("in_a_row", 1),
            pushing_only=raw.get("pushing_only", True), group=raw.get("group", "agent"),
            enabled_by=raw.get("enabled_by"), status=raw.get("status", "draft"), meta=dict(raw.get("meta") or {}),
        )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "track": self.track, "car": self.car, "description": self.description,
                               "when": self.when}
        if self.condition is not None:
            out["if"] = self.condition.text
        out["actions"] = [a.to_dict() for a in self.actions]
        for key, value, default in (("limits", self.limits, {}), ("in_a_row", self.in_a_row, 1),
                                    ("pushing_only", self.pushing_only, True), ("group", self.group, "agent"),
                                    ("enabled_by", self.enabled_by, None)):
            if value != default:
                out[key] = value
        out["status"] = self.status
        if self.meta:
            out["meta"] = self.meta
        return out

    @property
    def fingerprint(self) -> str:
        """Changes when anything that affects behaviour changes (not status or notes)."""
        body = {k: v for k, v in self.to_dict().items() if k not in ("status", "meta", "description")}
        return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]

    def applies_to(self, track: str, car: str) -> bool:
        return (self.track is None or self.track == track) and (self.car is None or self.car == car)

    def filters(self) -> dict:
        return {k: v for k, v in self.when.items() if k not in ("event", "where", *EXTRAS.get(self.trigger, {}))}

    def corners(self) -> set[int]:
        """The corners it names (to check against the track's map)."""
        corner_fields = EVENTS[self.trigger].corner_fields()
        out = set()
        for key, value in self.filters().items():
            if key in corner_fields:
                out |= set(value if isinstance(value, list) else [value])
        if isinstance(self.when.get("at"), dict) and "corner" in self.when["at"]:
            out.add(self.when["at"]["corner"])
        return out

    def names(self) -> set[str]:
        """The variables it reads (for the log)."""
        out: set[str] = set()
        for expr in (self.condition, self.where, self.edge):
            if expr is not None:
                out |= expr.names
        for a in self.actions:
            out |= a.names()
        return out


def resolve_at(at: dict, offset_m: float, cmap: CornerMap,
               cue_target: Callable[[int], float | None]) -> tuple[float, Corner | None]:
    """Where an `at` point is: (metres from the line, the corner if it was given as one)."""
    if "metres" in at:
        return float(at["metres"]) + offset_m, None
    corner = cmap.get(at["corner"])
    point = at.get("point", "brake")
    if point == "brake":
        target = cue_target(corner.id)
        target = corner.entry_m - 30.0 if target is None else target
    else:
        target = {"entry": corner.entry_m, "apex": corner.apex_m, "exit": corner.exit_m}[point]
    return target + offset_m, corner


def filter_sets(rule: Rule) -> dict[str, set]:
    """Field filters as sets of allowed values."""
    return {k: set(v if isinstance(v, list) else [v]) for k, v in rule.filters().items()}


def check_against_map(rule: Rule, cmap: CornerMap) -> None:
    """The corners a rule names must exist on the track (raises RuleError)."""
    known = {c.id for c in cmap.corners}
    missing = sorted(rule.corners() - known)
    if missing:
        listed = ", ".join(f"{c.id} ({c.name})" if c.name else str(c.id) for c in cmap.corners)
        raise RuleError(f"{rule.id}: no corner {', '.join(map(str, missing))} on {cmap.track_key}; it has {listed}. "
                        "See `iagent corners list --json`.")
