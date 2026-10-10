"""The rule format: parsing and checking a rule, and finding the corners it names.

    {
      "id": "bus-stop-early-brake",
      "track": "spa-2024-up",
      "description": "Brakes too early for the Bus Stop: say so on the straight after.",
      "when": {"event": "corner_exit", "corner": "Bus Stop"},
      "if": "brake_diff_m < -10",
      "action": {"say": "{name}: braked {round5(-brake_diff_m)} metres early.", "priority": "feedback"},
      "limits": {"cooldown_laps": 1}
    }

`when` names any event (`iagent.live.events`) and filters on its variables: a value, a list of
values, or "any"; corner fields take a number, "T9" or a name. `where` adds a condition to the
trigger itself. `approach` takes `at` (a point on track: metres, or a corner and a point, "T9",
"T9 apex", with `offset_m` and `lead_s`); `frame` takes `edge` (a condition, edge-triggered, with
`for_s` and `rearm_s`). `SHORTHAND` lists the short forms (`{"corner_exit": "T9"}`, ...).
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from iagent.analysis.corners import Corner, CornerMap
from iagent.live.events import EVENTS, Approach
from iagent.live.expr import Expr
from iagent.live.rules.actions import ACTIONS, Action, RuleError, expression, parse_action
from iagent.live.settings import Settings
from iagent.live.state import CoachState

POINTS = ("brake", "entry", "apex", "exit")
STATUSES = ("draft", "active", "archived")
GROUPS = ("agent", "coach")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ANY = ("any", "*", "all")

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

# What `when` takes beyond the event's own variables, per event.
EXTRAS = {"approach": {"at", "offset_m", "lead_s"}, "frame": {"edge", "for_s", "rearm_s"}}

# Short forms of `when`: key -> (its value, the rest of `when`) -> the general form.
SHORTHAND: dict[str, Callable[[Any, dict], dict]] = {
    "at": lambda v, w: {"event": "approach", "at": v, **w},
    "corner_exit": lambda v, w: {"event": "corner_exit", "corner": v, **w},
    "lap": lambda v, w: {"event": "lap", **w},
    "pit": lambda v, w: {"event": "pit", "pit": v, **w},
    "pace": lambda v, w: {"event": "pace", **({} if v in ANY else {"mode": v}), **w},
    "focus": lambda v, w: {"event": "focus", **w},
    "condition": lambda v, w: {"event": "frame", "edge": v, **w},
    "every_s": lambda v, w: {"event": "clock", "where": f"since_start_s % {v} == 0 and since_start_s > 0", **w},
    "at_s": lambda v, w: {"event": "clock", "where": f"since_start_s == {int(v)}", **w},
    "every_laps": lambda v, w: {"event": "lap", "where": f"lap % {int(v)} == 0", **w},
    "at_lap": lambda v, w: {"event": "lap", "where": f"lap == {int(v)}", **w},
    "session_start": lambda v, w: {"event": "session_start", **w},
}
LIMITS = ("cooldown_s", "cooldown_laps", "max_per_lap", "max_per_session", "once")


def variables(event: str) -> set[str]:
    """Every name a rule on EVENT can use: the shared state, the live channels, the event's own."""
    return set(CoachState.docs()) | set(CHANNELS) | set(EVENTS[event].variable_docs())


@dataclass
class Rule:
    id: str
    when: dict  # as written
    match: dict  # the general form: {"event", field filters, "where", extras}
    actions: list[Action]
    trigger: str  # the event's name
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

    @classmethod
    def from_dict(cls, raw: dict) -> "Rule":
        if not isinstance(raw, dict):
            raise RuleError("A rule is a JSON object.")
        known = {"id", "track", "car", "description", "when", "if", "action", "actions", "limits", "in_a_row",
                 "pushing_only", "status", "meta", "group", "enabled_by"}
        if unknown := set(raw) - known:
            raise RuleError(f"Unknown field(s) {', '.join(sorted(unknown))}; a rule has: {', '.join(sorted(known))}.")
        rule_id = raw.get("id")
        if not rule_id or not ID_RE.match(str(rule_id)):
            raise RuleError("A rule needs an id: lowercase letters, digits, '-' or '_' (e.g. \"pouhon-wide\").")
        when = raw.get("when")
        if not isinstance(when, dict):
            raise RuleError(f"{rule_id}: `when` is an object: {{\"event\": ..., filters}} (`iagent rules vars` lists events).")
        match = _normalize(rule_id, when)
        event = match["event"]
        names = variables(event)
        raw_actions = raw.get("actions", raw.get("action"))
        if isinstance(raw_actions, dict):
            raw_actions = [raw_actions]
        if not raw_actions or not isinstance(raw_actions, list):
            raise RuleError(f"{rule_id}: needs an action: one of {', '.join(ACTIONS)}.")
        limits = raw.get("limits") or {}
        if not isinstance(limits, dict) or set(limits) - set(LIMITS):
            raise RuleError(f"{rule_id}: limits can be {', '.join(LIMITS)}.")
        for k, v in limits.items():
            if k == "once" and not isinstance(v, bool):
                raise RuleError(f"{rule_id}: limits.once is true or false.")
            if k != "once" and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0):
                raise RuleError(f"{rule_id}: limits.{k} must be a number >= 0.")
        in_a_row = raw.get("in_a_row", 1)
        if not isinstance(in_a_row, int) or isinstance(in_a_row, bool) or in_a_row < 1:
            raise RuleError(f"{rule_id}: in_a_row is a whole number >= 1.")
        for key, allowed in (("status", STATUSES), ("group", GROUPS)):
            if raw.get(key, allowed[0]) not in allowed:
                raise RuleError(f"{rule_id}: {key} is one of {', '.join(allowed)}.")
        enabled_by = raw.get("enabled_by")
        if enabled_by is not None and enabled_by not in Settings.__dataclass_fields__:
            raise RuleError(f"{rule_id}: enabled_by names a setting; there's no {enabled_by!r}.")
        cond = raw.get("if")
        return cls(
            id=rule_id, when=dict(when), match=match, trigger=event,
            actions=[parse_action(rule_id, a, event, names) for a in raw_actions],
            track=raw.get("track") or None, car=raw.get("car") or None, description=str(raw.get("description") or ""),
            condition=expression(rule_id, cond, names) if cond not in (None, "") else None,
            where=expression(rule_id, match["where"], names) if match.get("where") else None,
            edge=expression(rule_id, match["edge"], names) if match.get("edge") else None,
            limits=dict(limits), in_a_row=in_a_row, pushing_only=bool(raw.get("pushing_only", True)),
            group=raw.get("group", "agent"), enabled_by=enabled_by, status=raw.get("status", "draft"),
            meta=dict(raw.get("meta") or {}),
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
        return {k: v for k, v in self.match.items() if k not in ("event", "where", *EXTRAS.get(self.trigger, ()))}

    def names(self) -> set[str]:
        """The variables it reads (for the log)."""
        out: set[str] = set()
        for expr in (self.condition, self.where, self.edge):
            if expr is not None:
                out |= expr.names
        for a in self.actions:
            out |= a.names()
        return out


def _normalize(rule_id: str, when: dict) -> dict:
    keys = [k for k in when if k in SHORTHAND]
    if "event" in when and keys:
        raise RuleError(f"{rule_id}: `when` has both `event` and the shorthand {keys[0]!r}.")
    if len(keys) > 1:
        raise RuleError(f"{rule_id}: `when` needs exactly one trigger (got {', '.join(keys)}).")
    if keys:
        match = SHORTHAND[keys[0]](when[keys[0]], {k: v for k, v in when.items() if k != keys[0]})
    elif "event" in when:
        match = dict(when)
    else:
        raise RuleError(f"{rule_id}: `when` needs an event: {{\"event\": ...}}, or a shorthand ({', '.join(SHORTHAND)}).")
    event = match["event"]
    if event not in EVENTS:
        raise RuleError(f"{rule_id}: no event {event!r}; there are: {', '.join(sorted(EVENTS))}.")
    fields = set(EVENTS[event].variable_docs())
    for key, value in match.items():
        if key in ("event", "where") or key in EXTRAS.get(event, ()):
            continue
        if key not in fields:
            raise RuleError(f"{rule_id}: `{event}` has no field {key!r} to filter on "
                            f"(it has: {', '.join(sorted(fields)) or 'none'}).")
        items = value if isinstance(value, list) else [value]
        if not items or any(isinstance(v, (dict, list)) for v in items):
            raise RuleError(f"{rule_id}: filter {key!r} is a value, a list of values, or \"any\".")
    for key in ("offset_m", "lead_s", "for_s", "rearm_s"):
        if key in match and (isinstance(match[key], bool) or not isinstance(match[key], (int, float))):
            raise RuleError(f"{rule_id}: {key} must be a number.")
    if "at" in match and (isinstance(match["at"], bool) or not isinstance(match["at"], (int, float, str))):
        raise RuleError(f"{rule_id}: `at` is metres from the line (1234) or a corner (\"T9\", \"T9 apex\").")
    if event == Approach.name and not ({"at", "watch", "source"} & set(match)):
        raise RuleError(f"{rule_id}: an `approach` rule needs `at` (a point), or a `source`/`watch` filter.")
    return match


# --- corners by number or name ------------------------------------------------------------------

def find_corner(cmap: CornerMap, ref: Any) -> Corner:
    """9, "9", "T9", "Turn 9" or a corner's name (case and spacing don't matter)."""
    if isinstance(ref, int):
        return cmap.get(ref)
    number = re.fullmatch(r"(?:t|turn)?\s*(\d+)", str(ref).strip().lower())
    if number:
        return cmap.get(int(number.group(1)))
    key = _norm(str(ref))
    for c in cmap.corners:
        if c.name and _norm(c.name) == key:
            return c
    raise KeyError(f"No corner {ref!r} on {cmap.track_key} (corners: {', '.join(c.label for c in cmap.corners)}).")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def resolve_at(match: dict, cmap: CornerMap, cue_target: Callable[[int], float | None]) -> tuple[float, Corner | None]:
    """Where an `at` point is: (metres from the line, the corner if it was given as one)."""
    value, offset = match["at"], float(match.get("offset_m", 0.0))
    if isinstance(value, (int, float)):
        return float(value) + offset, None
    text, point = value.strip(), "brake"
    words = text.rsplit(None, 1)
    if len(words) == 2 and words[1].lower() in POINTS:
        text, point = words[0], words[1].lower()
    corner = find_corner(cmap, text)
    if point == "brake":
        at = cue_target(corner.id)
        at = corner.entry_m - 30.0 if at is None else at
    else:
        at = {"entry": corner.entry_m, "apex": corner.apex_m, "exit": corner.exit_m}[point]
    return at + offset, corner


def resolve_filters(rule: Rule, cmap: CornerMap) -> dict[str, set | None]:
    """Field filters as sets of allowed values (None: any), corner names resolved."""
    corners = EVENTS[rule.trigger].corner_fields()
    out = {}
    for key, value in rule.filters().items():
        items = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and v.strip().lower() in ANY for v in items):
            out[key] = None
        elif key in corners:
            out[key] = {find_corner(cmap, v).id for v in items}
        else:
            out[key] = set(items)
    return out


def uses_corners(rule: Rule) -> bool:
    if rule.trigger == Approach.name and isinstance(rule.match.get("at"), str):
        return True
    corners = EVENTS[rule.trigger].corner_fields()
    return any(k in corners and not (isinstance(v, str) and v.lower() in ANY) for k, v in rule.filters().items())


def check_against_map(rule: Rule, cmap: CornerMap) -> None:
    """Corner references must exist on the track (raises RuleError)."""
    try:
        if "at" in rule.match:
            resolve_at(rule.match, cmap, lambda _: None)
        resolve_filters(rule, cmap)
    except KeyError as e:
        raise RuleError(f"{rule.id}: {e.args[0]}") from None
