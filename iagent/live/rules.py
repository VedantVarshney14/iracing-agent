"""Rules: what to do when an event happens. Everything the coach says goes through them.

A rule is data: an event (`when`), an optional condition (`if`, see `iagent.live.expr`), one or
more actions and limits. The coach's own behaviour (corner cues, feedback, the lap summary, the
focus news, the debrief) is a set of built-in rules (`builtin_rules`); the agent adds its own
through the CLI. The live pipeline hands every event to the engine, which evaluates the rules for
it deterministically, with no model involved:

    {
      "id": "bus-stop-early-brake",
      "track": "spa-2024-up",
      "description": "Brakes too early for the Bus Stop: say so on the straight after.",
      "when": {"event": "corner_exit", "corner": "Bus Stop"},
      "if": "brake_diff_m < -10",
      "action": {"say": "{name}: braked {round5(-brake_diff_m)} metres early.", "priority": "feedback"},
      "limits": {"cooldown_laps": 1}
    }

`when` names any defined event (`iagent rules vars` lists them, with their fields) and filters on
its fields: a value, a list of values, or "any"; corner fields take a number, "T9" or a name.
`where` adds a condition to the trigger itself. Two events take extras:

- `approach` with `at`: a point on track, 1234 (metres) or a corner and a point ("T9", "T9 apex",
  "Pouhon exit"; brake (default: the reference brake point), entry, apex, exit), with `offset_m`
  and `lead_s`. A spoken line is started so it finishes `lead_s` before the point.
- `frame` with `edge`: a condition over the live channels, fired when it becomes true (and has
  stayed true `for_s`), re-armed once it has been false for `rearm_s`.

Shorthand: `{"corner_exit": "T9"}`, `{"at": "T9"}`, `{"lap": "complete"}`, `{"pit": "entry"}`,
`{"pace": "tranquille"}`, `{"focus": "change"}`, `{"condition": "..."}`, `{"every_s": 300}`,
`{"at_s": 600}`, `{"every_laps": 5}`, `{"at_lap": 3}`, `{"session_start": true}` (`SHORTHAND`).

Actions (`ACTIONS`): `say` (with `long`, `priority`, `kind`, `expires_s`), `wake` (the coach is
asked, in its own words), `log`, `emit` (another event). Limits: `cooldown_s`, `cooldown_laps`,
`max_per_lap`, `max_per_session`, `once`; `in_a_row: N`; `pushing_only` (default true) ignores
events about driving while the driver isn't pushing. Groups (`GROUPS`) set limits for a whole set
of rules: the agent's rules share a budget of lines a lap, the coach's own don't.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from iagent.analysis.corners import Corner, CornerMap
from iagent.live import components as _components  # noqa: F401  (defines the events and state rules use)
from iagent.live.events import EVENTS, STATE, define_event
from iagent.live.expr import FUNCTIONS, Expr, ExprError, Template
from iagent.live.pipeline import Component
from iagent.live.settings import Settings
from iagent.live.speech import APPROACH, FEEDBACK, SUMMARY, Utterance

logger = logging.getLogger("iagent.live")

PRIORITIES = {"cue": APPROACH, "feedback": FEEDBACK, "answer": FEEDBACK, "summary": SUMMARY}
POINTS = ("brake", "entry", "apex", "exit")
STATUSES = ("draft", "active", "archived")
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

# What `when` takes beyond the event's own fields, per event.
EXTRAS = {"approach": {"at", "offset_m", "lead_s"}, "frame": {"edge", "for_s", "rearm_s"}}

# Shorthand triggers: key -> (its value, the rest of `when`) -> the general form.
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


@dataclass(frozen=True)
class Group:
    max_lines_per_lap: int | None = None  # lines said a lap, all the group's rules together
    min_gap_s: float = 0.0  # between two of the group's lines (cues aside)
    report: bool = True  # firings go in the session log as `rule` events


GROUPS = {"agent": Group(max_lines_per_lap=6, min_gap_s=4.0), "coach": Group(report=False)}

define_event("rule", "A rule fired, or a limit held it back.", {
    "rule": "the rule", "result": '"fired" or "limited"', "why": "the limit that held it",
    "trigger": "the event it fired on", "values": "the values it looked at", "actions": "what it did",
    "lap": "laps counted", "lap_no": "line crossings", "lap_dist": "where (m)"}, log=True)
define_event("say", "Say something (an answer to the driver's question, say).", {
    "text": "the words", "kind": "what it is (answer, ...)"})


def variables(event: str) -> set[str]:
    defn = EVENTS[event]
    return set(STATE) | set(CHANNELS) | {k for k, f in defn.fields.items() if not f.internal}


class RuleError(ValueError):
    pass


# --- actions ------------------------------------------------------------------------------------

@dataclass
class Action:
    kind: str  # a key of ACTIONS
    template: Template
    options: dict = field(default_factory=dict)  # parsed per action: priority, longer, ...

    def to_dict(self) -> dict:
        return {self.kind: self.template.text, **self.options.get("raw", {})}


@dataclass(frozen=True)
class ActionDef:
    doc: str
    options: tuple[str, ...]  # the fields it takes besides its own key
    parse: Callable[[str, dict, str, set[str]], dict]  # (rule id, raw, event, names) -> options
    run: Callable[["RuleEngine", "Rule", Action, str, dict, Any], dict]  # -> what it did


def _template(rule_id: str, text: Any, names: set[str]) -> Template:
    try:
        t = Template(str(text))
    except ExprError as e:
        raise RuleError(f"{rule_id}: {e}") from None
    _check_names(rule_id, t.names, names)
    return t


def _parse_say(rule_id: str, raw: dict, event: str, names: set[str]) -> dict:
    priority = raw.get("priority", "cue" if event == "approach" else "summary" if event == "lap" else "feedback")
    if priority not in PRIORITIES:
        raise RuleError(f"{rule_id}: priority is one of {', '.join(PRIORITIES)}.")
    expires = raw.get("expires_s")
    if isinstance(expires, str):
        expires = _expr(rule_id, expires, names)
    elif expires is not None and (isinstance(expires, bool) or not isinstance(expires, (int, float)) or expires <= 0):
        raise RuleError(f"{rule_id}: expires_s is a number of seconds > 0 (or an expression).")
    keep = {k: raw[k] for k in ("long", "priority", "kind", "expires_s") if k in raw}
    return {"priority": priority, "expires": expires, "raw": keep,
            "longer": _template(rule_id, raw["long"], names) if raw.get("long") else None,
            "kind": _template(rule_id, raw.get("kind", "rule"), names)}


def _run_say(engine, rule, action, text, env, e) -> dict:
    o = action.options
    priority = PRIORITIES[o["priority"]]
    held = engine.group_held(rule, priority, e.at)
    if held:
        return {"held": held}
    expires = o["expires"]
    if isinstance(expires, Expr):
        expires = expires(env)
    if expires is not None:
        until = e.at + float(expires)
    elif priority == APPROACH and e.get("expires_at") is not None:
        until = e["expires_at"]  # a cue that can't finish before its point is no use
    else:
        until = e.at + (10.0 if EVENTS[e.type].judged else 40.0)
    corner = env.get("corner") if isinstance(env.get("corner"), int) else None
    longer = o["longer"].render(env) if o["longer"] else None
    kind = o["kind"].render(env) or "rule"
    engine.ctx.arbiter.say(Utterance(text, priority, kind, e.at, until, corner, rule=e.get("rule") or rule.id,
                                     longer=longer))
    engine.said(rule, priority, e.at)
    return {}


def _run_wake(engine, rule, action, text, env, e) -> dict:
    from iagent.live.narrator import WAKE_MIN_GAP_S

    st = engine.state
    wake = {"rule": rule.id, "message": text, "values": engine.values(rule, env), "description": rule.description}
    engine.emit("wake", **wake)
    engine.emit("narrate", kind="wake", reply="coach_words", rule=rule.id, min_gap_s=WAKE_MIN_GAP_S, wake=wake,
                state={"mode": st["mode"], "lap": st["lap"], "focus_label": st["focus_label"],
                       "crewchief": engine.settings.crewchief})
    return {}


def _parse_emit(rule_id: str, raw: dict, event: str, names: set[str]) -> dict:
    target = raw["emit"]
    if target not in EVENTS:
        raise RuleError(f"{rule_id}: no event {target!r} to emit.")
    return {}


ACTIONS: dict[str, ActionDef] = {
    "say": ActionDef("say it (cue, feedback or summary priority; `long` for when there's time)",
                     ("long", "priority", "kind", "expires_s"), _parse_say, _run_say),
    "wake": ActionDef("wake the coach: it may answer on the radio in its own words", (),
                      lambda *a: {}, _run_wake),
    "log": ActionDef("note it in the session log", (), lambda *a: {}, lambda *a: {}),
    "emit": ActionDef("emit another event (its name), with this event's fields", (), _parse_emit,
                      lambda engine, rule, action, text, env, e: engine.emit(text, **e.public()) and {}),
}


# --- the rule -----------------------------------------------------------------------------------

@dataclass
class Rule:
    id: str
    when: dict  # as written
    match: dict  # the general form: {"event", field filters, "where", ...}
    actions: list[Action]
    trigger: str  # the event
    track: str | None = None
    car: str | None = None
    description: str = ""
    condition: Expr | None = None
    where: Expr | None = None
    edge: Expr | None = None
    limits: dict = field(default_factory=dict)
    in_a_row: int = 1
    pushing_only: bool = True
    group: str = "agent"
    enabled_by: str | None = None  # a Settings flag that switches it off
    status: str = "draft"
    meta: dict = field(default_factory=dict)  # created_by, created_at, backtest, ...

    @classmethod
    def from_dict(cls, raw: dict) -> "Rule":
        if not isinstance(raw, dict):
            raise RuleError("A rule is a JSON object.")
        known = {"id", "track", "car", "description", "when", "if", "action", "actions", "limits", "in_a_row",
                 "pushing_only", "status", "meta", "group", "enabled_by"}
        unknown = set(raw) - known
        if unknown:
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
        where = _expr(rule_id, match["where"], names) if match.get("where") else None
        edge = _expr(rule_id, match["edge"], names) if match.get("edge") else None
        condition = _expr(rule_id, raw.get("if"), names) if raw.get("if") not in (None, "") else None
        raw_actions = raw.get("actions", raw.get("action"))
        if isinstance(raw_actions, dict):
            raw_actions = [raw_actions]
        if not raw_actions or not isinstance(raw_actions, list):
            raise RuleError(f"{rule_id}: needs an action: one of {', '.join(ACTIONS)}.")
        actions = [_action(rule_id, a, event, names) for a in raw_actions]
        limits = raw.get("limits") or {}
        allowed = {"cooldown_s", "cooldown_laps", "max_per_lap", "max_per_session", "once"}
        if not isinstance(limits, dict) or set(limits) - allowed:
            raise RuleError(f"{rule_id}: limits can be {', '.join(sorted(allowed))}.")
        for k, v in limits.items():
            if k == "once":
                if not isinstance(v, bool):
                    raise RuleError(f"{rule_id}: limits.once is true or false.")
            elif not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                raise RuleError(f"{rule_id}: limits.{k} must be a number >= 0.")
        in_a_row = raw.get("in_a_row", 1)
        if not isinstance(in_a_row, int) or isinstance(in_a_row, bool) or in_a_row < 1:
            raise RuleError(f"{rule_id}: in_a_row is a whole number >= 1.")
        status = raw.get("status", "draft")
        if status not in STATUSES:
            raise RuleError(f"{rule_id}: status is one of {', '.join(STATUSES)}.")
        group = raw.get("group", "agent")
        if group not in GROUPS:
            raise RuleError(f"{rule_id}: group is one of {', '.join(GROUPS)}.")
        enabled_by = raw.get("enabled_by")
        if enabled_by is not None and enabled_by not in Settings.__dataclass_fields__:
            raise RuleError(f"{rule_id}: enabled_by names a setting; there's no {enabled_by!r}.")
        return cls(
            id=rule_id, when=dict(when), match=match, actions=actions, trigger=event, track=raw.get("track") or None,
            car=raw.get("car") or None, description=str(raw.get("description") or ""), condition=condition,
            where=where, edge=edge, limits=dict(limits), in_a_row=in_a_row,
            pushing_only=bool(raw.get("pushing_only", True)), group=group, enabled_by=enabled_by, status=status,
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
        return {k: v for k, v in self.match.items() if k not in ("event", "where") and k not in EXTRAS.get(self.trigger, ())}


def _normalize(rule_id: str, when: dict) -> dict:
    keys = [k for k in when if k in SHORTHAND]
    if "event" in when and keys:
        raise RuleError(f"{rule_id}: `when` has both `event` and the shorthand {keys[0]!r}.")
    if len(keys) > 1:
        raise RuleError(f"{rule_id}: `when` needs exactly one trigger (got {', '.join(keys)}).")
    if keys:
        rest = {k: v for k, v in when.items() if k != keys[0]}
        match = SHORTHAND[keys[0]](when[keys[0]], rest)
    elif "event" in when:
        match = dict(when)
    else:
        raise RuleError(f"{rule_id}: `when` needs an event: {{\"event\": ...}}, or a shorthand "
                        f"({', '.join(SHORTHAND)}).")
    event = match["event"]
    if event not in EVENTS:
        raise RuleError(f"{rule_id}: no event {event!r}; there are: {', '.join(sorted(EVENTS))}.")
    defn = EVENTS[event]
    public = {k for k, f in defn.fields.items() if not f.internal}
    for key, value in match.items():
        if key in ("event", "where"):
            continue
        if key in EXTRAS.get(event, ()):
            continue
        if key not in public:
            raise RuleError(f"{rule_id}: `{event}` has no field {key!r} to filter on "
                            f"(it has: {', '.join(sorted(public)) or 'none'}).")
        items = value if isinstance(value, list) else [value]
        if not items or any(isinstance(v, (dict, list)) for v in items):
            raise RuleError(f"{rule_id}: filter {key!r} is a value, a list of values, or \"any\".")
    for key in ("offset_m", "lead_s", "for_s", "rearm_s"):
        if key in match and (isinstance(match[key], bool) or not isinstance(match[key], (int, float))):
            raise RuleError(f"{rule_id}: {key} must be a number.")
    if "at" in match and (isinstance(match["at"], bool) or not isinstance(match["at"], (int, float, str))):
        raise RuleError(f"{rule_id}: `at` is metres from the line (1234) or a corner (\"T9\", \"T9 apex\").")
    if event == "approach" and "at" not in match and "watch" not in match and "source" not in match:
        raise RuleError(f"{rule_id}: an `approach` rule needs `at` (a point), or a `source`/`watch` filter.")
    return match


def _expr(rule_id: str, text: Any, names: set[str]) -> Expr:
    try:
        expr = Expr(str(text))
    except ExprError as e:
        raise RuleError(f"{rule_id}: {e}") from None
    _check_names(rule_id, expr.names, names)
    return expr


def _check_names(rule_id: str, used: set[str], names: set[str]) -> None:
    unknown = sorted(used - names)
    if unknown:
        raise RuleError(f"{rule_id}: unknown variable(s) {', '.join(unknown)}. See `iagent rules vars` for what each "
                        "event provides.")


def _action(rule_id: str, raw: Any, event: str, names: set[str]) -> Action:
    if not isinstance(raw, dict):
        raise RuleError(f"{rule_id}: an action is an object like {{\"say\": \"...\"}}.")
    kinds = [k for k in ACTIONS if k in raw]
    if len(kinds) != 1:
        raise RuleError(f"{rule_id}: each action is one of {', '.join(ACTIONS)}.")
    kind = kinds[0]
    defn = ACTIONS[kind]
    extra = set(raw) - {kind} - set(defn.options)
    if extra:
        raise RuleError(f"{rule_id}: `{kind}` doesn't take {', '.join(sorted(extra))}.")
    template = _template(rule_id, raw[kind], names)
    if not template.text.strip():
        raise RuleError(f"{rule_id}: `{kind}` is empty.")
    return Action(kind, template, defn.parse(rule_id, raw, event, names))


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
    names = ", ".join(c.label for c in cmap.corners)
    raise KeyError(f"No corner {ref!r} on {cmap.track_key} (corners: {names}).")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def resolve_at(match: dict, cmap: CornerMap, cue_target: Callable[[int], float | None]) -> tuple[float, Corner | None]:
    """Where an `at` point is: (metres from the line, the corner if it was given as one)."""
    value = match["at"]
    offset = float(match.get("offset_m", 0.0))
    if isinstance(value, (int, float)):
        return float(value) + offset, None
    text = value.strip()
    point = "brake"
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
    defn = EVENTS[rule.trigger]
    out = {}
    for key, value in rule.filters().items():
        items = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and v.strip().lower() in ANY for v in items):
            out[key] = None
        elif defn.fields[key].type == "corner":
            out[key] = {find_corner(cmap, v).id for v in items}
        else:
            out[key] = set(items)
    return out


def uses_corners(rule: Rule) -> bool:
    if rule.trigger == "approach" and isinstance(rule.match.get("at"), str):
        return True
    defn = EVENTS[rule.trigger]
    return any(defn.fields[k].type == "corner" and not (isinstance(v, str) and v.lower() in ANY)
               for k, v in rule.filters().items())


def check_against_map(rule: Rule, cmap: CornerMap) -> None:
    """Corner references must exist on the track (raises RuleError)."""
    try:
        if "at" in rule.match:
            resolve_at(rule.match, cmap, lambda _: None)
        resolve_filters(rule, cmap)
    except KeyError as e:
        raise RuleError(f"{rule.id}: {e.args[0]}") from None


# --- the engine ---------------------------------------------------------------------------------

@dataclass
class RuleStats:
    occurrences: int = 0  # the event happened (and passed the filters)
    skipped: int = 0  # ... while not pushing (pushing_only)
    matched: int = 0  # ... and the condition held (in_a_row times)
    fired: int = 0
    limited: int = 0  # matched, but a limit held it back


@dataclass
class _State:
    last_fired_s: float | None = None
    last_fired_lap: int | None = None
    per_lap: dict[int, int] = field(default_factory=dict)
    fired: int = 0
    streak: int = 0
    armed: bool = True  # edge rules
    true_since: float | None = None
    false_since: float | None = None


@dataclass
class _GroupState:
    lines: dict[int, int] = field(default_factory=dict)  # lap_no -> lines said
    last_line_s: float = -1e9


class RuleEngine(Component):
    """Evaluates every rule on the events it names. The coach's built-in rules come from
    `builtin_rules`, the agent's from the context (`ctx.rules`, replaceable mid-session)."""

    def start(self):
        from iagent.live.builtin_rules import builtin_rules

        self.stats: dict[str, RuleStats] = {}
        self.firings: list[dict] = []
        self.errors: list[str] = []
        self._st: dict[str, _State] = {}
        self._groups: dict[str, _GroupState] = {g: _GroupState() for g in GROUPS}
        self._builtin = [Rule.from_dict(r) for r in builtin_rules(self.settings)]
        self.rules: list[Rule] = []  # the agent's, as live
        self._by_event: dict[str, list[tuple[Rule, dict]]] = {}
        self._watches: set[str] = set()
        self.replace(list(self.ctx.rules or []))

    def replace(self, rules: list[Rule]) -> None:
        """Swap in the agent's rules (e.g. one activated mid-session); counters carry over."""
        cmap, session = self.ctx.cmap, self.ctx.session
        for watch in self._watches:
            self.emit("unwatch", id=watch)
        self._watches = set()
        by_event: dict[str, list[tuple[Rule, dict]]] = {}
        kept, errors = [], []
        for rule in [*self._builtin, *rules]:
            if not rule.applies_to(session.track_key, session.car_key):
                continue
            if rule.enabled_by and not getattr(self.settings, rule.enabled_by):
                continue
            try:
                filters = resolve_filters(rule, cmap)
                if "at" in rule.match:
                    filters["watch"] = {self._watch(rule)}
            except KeyError as e:
                errors.append(f"{rule.id}: {e.args[0]}")
                continue
            by_event.setdefault(rule.trigger, []).append((rule, filters))
            self.stats.setdefault(rule.id, RuleStats())
            self._st.setdefault(rule.id, _State())
            if rule.group == "agent":
                kept.append(rule)
        for msg in errors:
            if msg not in self.errors:
                logger.warning("Rule skipped: %s", msg)
        self.errors, self.rules, self._by_event = errors, kept, by_event

    def _watch(self, rule: Rule) -> str:
        """A rule on a point on track: watched like the corner cues, timed by what it will say."""
        plan = self.ctx.plan
        target, corner = resolve_at(rule.match, self.ctx.cmap,
                                    lambda c: (cue.target_m if (cue := plan.cue_for(c)) is not None else None))
        watch = f"rule:{rule.id}"
        tags = {"source": "rule"}
        if corner is not None:
            tags.update(corner=corner.id, name=corner.name or f"Turn {corner.id}")
        says = [a for a in rule.actions if a.kind == "say"]
        lead = float(rule.match.get("lead_s", 0.8 if says else 0.0))

        def speak() -> float:
            env = {**self.pipe.channels, **self.state, **tags, "target_m": target}
            longest = 0.0
            for a in says:
                if text := a.template.render(env):
                    longest = max(longest, self.ctx.arbiter.voice.duration(text) or Utterance(text, 0, "", 0, 0).length_s)
            return longest
        self.emit("watch", id=watch, target_m=target, lead_s=lead, speak=speak, tags=tags)
        self._watches.add(watch)
        return watch

    # --- events --------------------------------------------------------------------------------

    def on_any(self, e):
        rules = self._by_event.get(e.type)
        if not rules:
            return
        env = None
        for rule, filters in rules:
            if any(allowed is not None and e.get(key) not in allowed for key, allowed in filters.items()):
                continue
            if env is None:
                env = {**self.pipe.channels, **self.state, **e.public()}
            if rule.where is not None and not rule.where(env):
                continue
            if rule.edge is not None and not self._edge(rule, env, e.at):
                continue
            self._occur(rule, env, e)

    def _edge(self, rule: Rule, env: dict, now: float) -> bool:
        st = self._st[rule.id]
        holds = bool(rule.edge(env))
        for_s, rearm_s = float(rule.match.get("for_s", 0.0)), float(rule.match.get("rearm_s", 1.0))
        if holds:
            st.false_since = None
            st.true_since = now if st.true_since is None else st.true_since
            if st.armed and now - st.true_since >= for_s:
                st.armed = False
                return True
        else:
            st.true_since = None
            st.false_since = now if st.false_since is None else st.false_since
            if not st.armed and now - st.false_since >= rearm_s:
                st.armed = True
        return False

    def _occur(self, rule: Rule, env: dict, e) -> None:
        stats, st = self.stats[rule.id], self._st[rule.id]
        stats.occurrences += 1
        if rule.pushing_only and EVENTS[e.type].judged and not env.get("pushing"):
            stats.skipped += 1
            return
        if rule.condition is not None and not rule.condition(env):
            st.streak = 0
            return
        st.streak += 1
        if st.streak < rule.in_a_row:
            return
        stats.matched += 1
        lap_no = self.state["laps_driven"]
        record = {"rule": rule.id, "at": round(e.at, 2), "lap": self.state["lap"], "lap_no": lap_no,
                  "lap_dist": None if env.get("lap_dist") is None else round(env["lap_dist"]),
                  "trigger": rule.trigger, "values": self.values(rule, env)}
        held = self._held(rule, st, e.at)
        if held:
            stats.limited += 1
            record.update({"result": "limited", "why": held})
            self._report(rule, record)
            return
        if rule.in_a_row > 1:
            st.streak = 0
        st.fired += 1
        st.last_fired_s, st.last_fired_lap = e.at, lap_no
        st.per_lap[lap_no] = st.per_lap.get(lap_no, 0) + 1
        stats.fired += 1
        done = []
        for a in rule.actions:
            text = a.template.render(env)
            if text is None:
                done.append({a.kind: None, "missing": a.template.missing(env)})
                continue
            done.append({a.kind: text, **(ACTIONS[a.kind].run(self, rule, a, text, env, e) or {})})
        record.update({"result": "fired", "actions": done})
        self._report(rule, record)

    def group_held(self, rule: Rule, priority: int, now: float) -> str | None:
        g, gs = GROUPS[rule.group], self._groups[rule.group]
        lines = gs.lines.get(self.state["laps_driven"], 0)
        if g.max_lines_per_lap is not None and lines >= g.max_lines_per_lap:
            return f"{lines} rule lines this lap already"
        if priority != APPROACH and now - gs.last_line_s < g.min_gap_s:
            return "another rule spoke just now"
        return None

    def said(self, rule: Rule, priority: int, now: float) -> None:
        gs = self._groups[rule.group]
        lap_no = self.state["laps_driven"]
        gs.lines[lap_no] = gs.lines.get(lap_no, 0) + 1
        gs.last_line_s = now

    def _held(self, rule: Rule, st: _State, now: float) -> str | None:
        lim, lap_no = rule.limits, self.state["laps_driven"]
        if lim.get("once") and st.fired >= 1:
            return "once only"
        if "max_per_session" in lim and st.fired >= lim["max_per_session"]:
            return f"{st.fired} time(s) this session already"
        if "max_per_lap" in lim and st.per_lap.get(lap_no, 0) >= lim["max_per_lap"]:
            return "enough this lap"
        if "cooldown_s" in lim and st.last_fired_s is not None and now - st.last_fired_s < lim["cooldown_s"]:
            return f"cooldown ({lim['cooldown_s']:g} s)"
        if "cooldown_laps" in lim and st.last_fired_lap is not None and lap_no - st.last_fired_lap <= lim["cooldown_laps"]:
            return f"cooldown ({lim['cooldown_laps']:g} lap(s))"
        return None

    def values(self, rule: Rule, env: dict) -> dict:
        """The values the rule looked at (for the log, backtests and the agent)."""
        names: set[str] = set()
        for expr in (rule.condition, rule.where, rule.edge):
            if expr is not None:
                names |= expr.names
        for a in rule.actions:
            names |= a.template.names
            if a.options.get("longer"):
                names |= a.options["longer"].names
        out = {}
        for n in sorted(names):
            v = env.get(n)
            if isinstance(v, (set, dict, list)):
                continue
            out[n] = round(v, 3) if isinstance(v, float) else v
        return out

    def _report(self, rule: Rule, record: dict) -> None:
        self.firings.append(record)
        if GROUPS[rule.group].report:
            self.emit("rule", **record)


def describe() -> dict:
    """Everything a rule can use, for `iagent rules vars` (and the agent): from the definitions."""
    return {
        "events": {name: {"doc": d.doc, "fields": {k: f.doc for k, f in d.fields.items() if not f.internal},
                          "judged": d.judged, "extras": sorted(EXTRAS.get(name, ()))}
                   for name, d in sorted(EVENTS.items())},
        "state": dict(STATE),
        "channels": dict(CHANNELS),
        "functions": {k: v[1] for k, v in FUNCTIONS.items()},
        "actions": {k: v.doc for k, v in ACTIONS.items()},
        "shorthand": list(SHORTHAND),
        "priorities": list(PRIORITIES),
        "limits": ["cooldown_s", "cooldown_laps", "max_per_lap", "max_per_session", "once"],
    }
