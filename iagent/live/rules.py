"""Rules: what the coach (the agent) asks the live service to watch for, and what to do then.

A rule is data, not code: a trigger (`when`), an optional condition (`if`, see `iagent.live.expr`),
one or more actions and limits. The agent writes rules through the CLI between runs; the live
coach evaluates them deterministically, with no model involved, every frame:

    {
      "id": "bus-stop-early-brake",
      "track": "spa-2024-up",
      "description": "Brakes too early for the Bus Stop: say so on the straight after.",
      "when": {"corner_exit": "Bus Stop"},
      "if": "brake_diff_m < -10",
      "action": {"say": "{name}: braked {round5(-brake_diff_m)} metres early.", "priority": "feedback"},
      "limits": {"cooldown_laps": 1}
    }

Triggers (`when`, exactly one of):

- `at`: a point on track, `1234` (metres) or a corner with an optional point, `"T9"`, `"T9 apex"`,
  `"Pouhon exit"` (points: brake (default: the reference brake point, as cues use), entry, apex,
  exit), plus `offset_m` and `lead_s`. A spoken cue is started so it *finishes* `lead_s` before
  the point at the current speed, like the corner cues.
- `corner_exit`: a corner (or a list, or `"any"`), shortly after its exit, with that corner's
  metrics against the reference lap.
- `lap`: `"complete"`, at the line after each counted lap.
- `pit`: `"entry"` or `"exit"`.
- `pace`: `"pushing"`, `"tranquille"` or `"any"`: the coach's judgement of the driver's pace changed.
- `focus`: `"change"`: the coach's focus changed.
- `condition`: an expression over the live channels, edge-triggered: fires when it becomes true
  (and has stayed true `for_s`), re-arms once it has been false for `rearm_s`.
- `every_s` / `at_s` (seconds since the coach started), `every_laps` / `at_lap` (counted laps),
  `session_start: true`.

Actions (`action`: one, or a list): `{"say": template, "long": template, "priority":
"cue"|"feedback"|"summary", "expires_s"}` (`long`, optional: the fuller version, said instead
when the driver isn't pushing and there's room), `{"wake": template}` (hand the event to the
agent), `{"log": template}`.

Limits: `cooldown_s`, `cooldown_laps` (laps to stay quiet after firing), `max_per_lap`,
`max_per_session`, `once`. `in_a_row: N` fires only when the condition held on N occurrences in a
row (e.g. ran wide at Pouhon two laps running). `pushing_only` (default true) ignores corners,
laps and track positions while the driver isn't pushing (out laps, cool-downs, after a moment).

`iagent rules vars` lists every variable each trigger provides.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from iagent.analysis.corners import Corner, CornerMap
from iagent.live.expr import FUNCTIONS, Expr, ExprError, Template
from iagent.live.speech import APPROACH, FEEDBACK, SUMMARY, Utterance

logger = logging.getLogger("iagent.live")

PRIORITIES = {"cue": APPROACH, "feedback": FEEDBACK, "summary": SUMMARY}
POINTS = ("brake", "entry", "apex", "exit")
STATUSES = ("draft", "active", "archived")
REARM_PAST_M = 100.0  # an `at` trigger fires again once the car is this far past its point
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# --- what each trigger provides ------------------------------------------------------------------

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
BASE_VARS = {
    "time": "session time (s)",
    "since_start_s": "seconds since the coach started",
    "lap": "laps counted so far (complete laps from the line, not on pit road)",
    "lap_dist": "distance from the line (m)",
    "speed_kph": "current speed (km/h)",
    "pushing": "true while the driver is pushing (the coach's pace judgement)",
    "mode": '"pushing" or "tranquille"',
    "learning": "true during the learning laps (every corner cued)",
    "focus": "the focus cue's first corner, or none",
    "focus_label": 'the focus as said ("Turns 15 and 16"), or none',
    "best_lap": "the driver's best lap time here (s), or none",
    "ref_lap_time": "the reference lap's time (s)",
    "track": "track key", "car": "car key",
}
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
TRIGGER_VARS: dict[str, dict[str, str]] = {
    "at": {"target_m": "the point (m)", "corner": "the corner (if given by corner)", "name": "its name or 'Turn N'"},
    "corner_exit": {
        "corner": "corner number", "name": "its name, or 'Turn N'", "delta_s": "time lost (+) or gained (-) vs the reference",
        "in_focus": "true if it's (part of) the focus", "struggling": "the coach thinks it went badly",
        "advice": "the coach's own advice for it, or none", "hint": "the coach's hint for next lap, or none",
        "at_pace": "true if the corner was driven pushing on a counted lap",
        **METRICS, **{f"ref_{k}": f"the reference lap's {k}" for k in METRICS},
        "brake_diff_m": "brake_m - ref_brake_m: > 0 braked later",
        "min_speed_diff_kph": "min_speed_kph - ref_min_speed_kph: > 0 carried more speed",
        "throttle_diff_m": "full_throttle_m - ref_full_throttle_m: > 0 on full throttle later",
        "exit_speed_diff_kph": "exit_speed_kph - ref_exit_speed_kph",
    },
    "lap": {
        "lap_time": "s", "gap_s": "vs the reference (+ slower)", "best_gap_s": "vs the best lap before this one (+ slower)",
        "new_best": "true if it's a new best", "pace": '"pushing", "moment" or "tranquille"',
        "pushing_share": "share of the lap pushing (0-1)", "moment_at": "corner where a moment started, or none",
        "worst_corner": "corner that lost the most", "worst_name": "its name", "worst_delta_s": "how much it lost",
    },
    "pit": {"pit": '"entry" or "exit"'},
    "pace": {"mode": 'the new mode: "pushing" or "tranquille"'},
    "focus": {"focus": "new focus cue corner, or none", "focus_label": "as said", "previous_focus": "the one before, or none"},
    "condition": {},
    "every_s": {"count": "times fired before"}, "at_s": {}, "every_laps": {"count": "times fired before"},
    "at_lap": {}, "session_start": {},
}
TRIGGERS = tuple(TRIGGER_VARS)
PUSHING_TRIGGERS = ("at", "corner_exit", "lap", "condition")  # the ones `pushing_only` applies to


def variables(trigger: str) -> set[str]:
    return set(BASE_VARS) | set(CHANNELS) | set(TRIGGER_VARS[trigger])


class RuleError(ValueError):
    pass


# --- the rule -----------------------------------------------------------------------------------

@dataclass
class Action:
    kind: str  # say, wake, log
    template: Template
    priority: str = "feedback"
    expires_s: float | None = None
    longer: Template | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {self.kind: self.template.text}
        if self.longer is not None:
            out["long"] = self.longer.text
        if self.kind == "say":
            out["priority"] = self.priority
            if self.expires_s is not None:
                out["expires_s"] = self.expires_s
        return out


@dataclass
class Rule:
    id: str
    when: dict
    actions: list[Action]
    trigger: str
    track: str | None = None
    car: str | None = None
    description: str = ""
    condition: Expr | None = None
    limits: dict = field(default_factory=dict)
    in_a_row: int = 1
    pushing_only: bool = True
    status: str = "draft"
    meta: dict = field(default_factory=dict)  # created_by, created_at, backtest, ...

    @classmethod
    def from_dict(cls, raw: dict) -> "Rule":
        if not isinstance(raw, dict):
            raise RuleError("A rule is a JSON object.")
        known = {"id", "track", "car", "description", "when", "if", "action", "actions", "limits",
                 "in_a_row", "pushing_only", "status", "meta"}
        unknown = set(raw) - known
        if unknown:
            raise RuleError(f"Unknown field(s) {', '.join(sorted(unknown))}; a rule has: {', '.join(sorted(known))}.")
        rule_id = raw.get("id")
        if not rule_id or not ID_RE.match(str(rule_id)):
            raise RuleError("A rule needs an id: lowercase letters, digits, '-' or '_' (e.g. \"pouhon-wide\").")
        when = raw.get("when")
        if not isinstance(when, dict):
            raise RuleError(f"{rule_id}: `when` is an object with one trigger: {', '.join(TRIGGERS)}.")
        triggers = [k for k in when if k in TRIGGERS]
        if len(triggers) != 1:
            raise RuleError(f"{rule_id}: `when` needs exactly one trigger out of {', '.join(TRIGGERS)} (got {sorted(when)}).")
        trigger = triggers[0]
        _check_when(rule_id, trigger, when)
        names = variables(trigger)
        condition = _expr(rule_id, raw.get("if"), names) if raw.get("if") not in (None, "") else None
        raw_actions = raw.get("actions", raw.get("action"))
        if isinstance(raw_actions, dict):
            raw_actions = [raw_actions]
        if not raw_actions or not isinstance(raw_actions, list):
            raise RuleError(f"{rule_id}: needs an action: {{\"say\": ...}}, {{\"wake\": ...}} or {{\"log\": ...}}.")
        actions = [_action(rule_id, a, trigger, names) for a in raw_actions]
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
        return cls(
            id=rule_id, when=dict(when), actions=actions, trigger=trigger, track=raw.get("track") or None,
            car=raw.get("car") or None, description=str(raw.get("description") or ""), condition=condition,
            limits=dict(limits), in_a_row=in_a_row, pushing_only=bool(raw.get("pushing_only", True)),
            status=status, meta=dict(raw.get("meta") or {}),
        )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"id": self.id, "track": self.track, "car": self.car, "description": self.description,
                               "when": self.when}
        if self.condition is not None:
            out["if"] = self.condition.text
        out["actions"] = [a.to_dict() for a in self.actions]
        if self.limits:
            out["limits"] = self.limits
        if self.in_a_row != 1:
            out["in_a_row"] = self.in_a_row
        if not self.pushing_only:
            out["pushing_only"] = False
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
                        "trigger provides.")


def _action(rule_id: str, raw: Any, trigger: str, names: set[str]) -> Action:
    if not isinstance(raw, dict):
        raise RuleError(f"{rule_id}: an action is an object like {{\"say\": \"...\"}}.")
    kinds = [k for k in ("say", "wake", "log") if k in raw]
    if len(kinds) != 1:
        raise RuleError(f"{rule_id}: each action is one of say, wake or log.")
    kind = kinds[0]
    extra = set(raw) - {kind, "priority", "expires_s"} - ({"long"} if kind == "say" else set())
    if extra:
        raise RuleError(f"{rule_id}: unknown action field(s) {', '.join(sorted(extra))}.")
    try:
        template = Template(str(raw[kind]))
    except ExprError as e:
        raise RuleError(f"{rule_id}: {e}") from None
    _check_names(rule_id, template.names, names)
    longer = None
    if raw.get("long"):
        try:
            longer = Template(str(raw["long"]))
        except ExprError as e:
            raise RuleError(f"{rule_id}: {e}") from None
        _check_names(rule_id, longer.names, names)
    if kind == "say" and not template.text.strip():
        raise RuleError(f"{rule_id}: nothing to say.")
    priority = raw.get("priority", "cue" if trigger == "at" else "summary" if trigger == "lap" else "feedback")
    if priority not in PRIORITIES:
        raise RuleError(f"{rule_id}: priority is one of {', '.join(PRIORITIES)}.")
    expires = raw.get("expires_s")
    if expires is not None and (not isinstance(expires, (int, float)) or expires <= 0):
        raise RuleError(f"{rule_id}: expires_s is a number of seconds > 0.")
    return Action(kind, template, priority, float(expires) if expires is not None else None, longer)


def _check_when(rule_id: str, trigger: str, when: dict) -> None:
    value = when[trigger]
    allowed_extra = {"at": {"offset_m", "lead_s"}, "condition": {"for_s", "rearm_s"}}.get(trigger, set())
    extra = set(when) - {trigger} - allowed_extra
    if extra:
        raise RuleError(f"{rule_id}: `{trigger}` doesn't take {', '.join(sorted(extra))}.")
    for k in allowed_extra & set(when):
        if not isinstance(when[k], (int, float)) or isinstance(when[k], bool):
            raise RuleError(f"{rule_id}: {k} must be a number.")
    if trigger == "at":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise RuleError(f"{rule_id}: `at` is metres from the line (1234) or a corner (\"T9\", \"T9 apex\").")
    elif trigger == "corner_exit":
        items = value if isinstance(value, list) else [value]
        if not items or any(isinstance(v, bool) or not isinstance(v, (int, str)) for v in items):
            raise RuleError(f"{rule_id}: `corner_exit` is a corner (9, \"T9\", a name), a list of them, or \"any\".")
    elif trigger == "lap" and value != "complete":
        raise RuleError(f"{rule_id}: `lap` is \"complete\".")
    elif trigger == "pit" and value not in ("entry", "exit"):
        raise RuleError(f"{rule_id}: `pit` is \"entry\" or \"exit\".")
    elif trigger == "pace" and value not in ("pushing", "tranquille", "any"):
        raise RuleError(f"{rule_id}: `pace` is \"pushing\", \"tranquille\" or \"any\".")
    elif trigger == "focus" and value != "change":
        raise RuleError(f"{rule_id}: `focus` is \"change\".")
    elif trigger == "condition":
        _expr(rule_id, value, variables("condition"))
    elif trigger in ("every_s", "at_s", "every_laps", "at_lap"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise RuleError(f"{rule_id}: `{trigger}` is a number > 0.")
    elif trigger == "session_start" and value is not True:
        raise RuleError(f"{rule_id}: `session_start` is true.")


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


def resolve_at(when: dict, cmap: CornerMap, cue_target: Callable[[int], float | None]) -> tuple[float, Corner | None]:
    """Where an `at` trigger is: (metres from the line, the corner if it was given as one)."""
    value = when["at"]
    offset = float(when.get("offset_m", 0.0))
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


def corner_ids(when: dict, cmap: CornerMap) -> set[int] | None:
    """The corners a `corner_exit` trigger watches (None: all of them)."""
    value = when["corner_exit"]
    items = value if isinstance(value, list) else [value]
    if any(isinstance(v, str) and v.strip().lower() in ("any", "*", "all") for v in items):
        return None
    return {find_corner(cmap, v).id for v in items}


def check_against_map(rule: Rule, cmap: CornerMap) -> None:
    """Corner references must exist on the track (raises RuleError)."""
    try:
        if rule.trigger == "at":
            resolve_at(rule.when, cmap, lambda _: None)
        elif rule.trigger == "corner_exit":
            corner_ids(rule.when, cmap)
    except KeyError as e:
        raise RuleError(f"{rule.id}: {e.args[0]}") from None


# --- the engine ---------------------------------------------------------------------------------

@dataclass
class EngineLimits:
    max_lines_per_lap: int = 6  # rule lines said per lap, all rules together
    min_gap_s: float = 4.0  # between two rule lines (other than cues)


@dataclass
class RuleStats:
    occurrences: int = 0  # the trigger happened
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
    armed: bool = True  # at / condition: ready to fire again
    true_since: float | None = None
    false_since: float | None = None
    next_s: float | None = None  # every_s


Fired = Callable[[dict], None]


class RuleEngine:
    """Evaluates rules against what the live coach sees. The coach calls in at each frame and
    on its events (corner exits, laps, pace and focus changes); the engine queues lines with the
    coach's arbiter and reports every firing to `on_fire` (and `wake` actions to `on_wake`)."""

    def __init__(self, rules: list[Rule], limits: EngineLimits | None = None):
        self.limits = limits or EngineLimits()
        self.rules: list[Rule] = []
        self.stats: dict[str, RuleStats] = {}
        self.on_fire: Fired | None = None
        self.on_wake: Fired | None = None
        self.firings: list[dict] = []
        self._coach = None
        self._state: dict[str, _State] = {}
        self._at: dict[str, tuple[float, Corner | None]] = {}
        self._corners: dict[str, set[int] | None] = {}
        self._env: dict[str, Any] = {}
        self._lap_no = 0  # every line crossing (limits count these)
        self._lines: dict[int, int] = {}  # lap_no -> rule lines said
        self._last_line_s = -1e9
        self._start_s: float | None = None
        self._prev_pit: bool | None = None
        self._prev_focus: Any = None
        self.errors: list[str] = []
        self._pending = list(rules)

    def attach(self, coach) -> None:
        """Bind to a coach (its corner map, cues and state); rules that don't fit it are dropped."""
        self._coach = coach
        self._prev_focus = coach.focus
        self.replace(self._pending)

    def replace(self, rules: list[Rule]) -> None:
        """Swap in a new set of rules (e.g. the agent activated one mid-session). Rules that
        stay keep their counters."""
        coach = self._coach
        self._pending = list(rules)
        if coach is None:
            return
        kept, errors = [], []
        track, car = coach.session.track_key, coach.session.car_key
        for rule in rules:
            if not rule.applies_to(track, car):
                continue
            try:
                if rule.trigger == "at":
                    self._at[rule.id] = resolve_at(rule.when, coach.cmap, self._cue_target)
                elif rule.trigger == "corner_exit":
                    self._corners[rule.id] = corner_ids(rule.when, coach.cmap)
            except KeyError as e:
                errors.append(f"{rule.id}: {e.args[0]}")
                continue
            kept.append(rule)
            self.stats.setdefault(rule.id, RuleStats())
            self._state.setdefault(rule.id, _State())
        for msg in errors:
            if msg not in self.errors:
                logger.warning("Rule skipped: %s", msg)
        self.errors = errors
        self.rules = kept

    def _cue_target(self, corner: int) -> float | None:
        cue = self._coach.plan.cue_for(corner)
        return None if cue is None else cue.target_m

    # --- what the coach calls ------------------------------------------------------------------

    def frame(self, frame, now: float, on_track: bool) -> None:
        coach = self._coach
        env = self._base(frame, now)
        if self._start_s is None:
            self._start_s = now
            env["since_start_s"] = 0.0
            for rule in self._of("session_start"):
                self._occur(rule, env, now)
        pit = bool(frame.get("OnPitRoad", 0))
        if self._prev_pit is not None and pit != self._prev_pit:
            for rule in self._of("pit"):
                if rule.when["pit"] == ("entry" if pit else "exit"):
                    self._occur(rule, {**env, "pit": "entry" if pit else "exit"}, now)
        self._prev_pit = pit
        if coach.focus != self._prev_focus:
            for rule in self._of("focus"):
                self._occur(rule, {**env, "previous_focus": self._prev_focus}, now)
            self._prev_focus = coach.focus
        for rule in self._of("condition"):
            self._condition(rule, env, now)
        for rule in self._of("every_s"):
            st = self._state[rule.id]
            period = float(rule.when["every_s"])
            if st.next_s is None:
                st.next_s = self._start_s + period
            if now >= st.next_s:
                st.next_s = now + period
                self._occur(rule, {**env, "count": st.fired}, now)
        for rule in self._of("at_s"):
            st = self._state[rule.id]
            if st.armed and env["since_start_s"] >= float(rule.when["at_s"]):
                st.armed = False
                self._occur(rule, env, now)
        if on_track:
            for rule in self._of("at"):
                self._position(rule, env, now)

    def corner_exit(self, result, at_pace: bool, now: float) -> None:
        coach = self._coach
        mine, ref = result.metrics or {}, coach._ref_metrics.get(result.corner) or {}
        c = coach.cmap.get(result.corner)
        cue = coach.plan.cue_for(result.corner)
        env = {**self._env, **{k: mine.get(k) for k in METRICS}, **{f"ref_{k}": ref.get(k) for k in METRICS},
               "corner": c.id, "name": c.name or f"Turn {c.id}", "delta_s": result.delta_s,
               "in_focus": cue is not None and cue.corner == coach.focus, "struggling": result.struggling,
               "advice": result.advice, "hint": result.hint, "at_pace": at_pace}
        for key, a, b in (("brake_diff_m", "brake_m", "ref_brake_m"), ("min_speed_diff_kph", "min_speed_kph", "ref_min_speed_kph"),
                          ("throttle_diff_m", "full_throttle_m", "ref_full_throttle_m"),
                          ("exit_speed_diff_kph", "exit_speed_kph", "ref_exit_speed_kph")):
            env[key] = None if env[a] is None or env[b] is None else round(env[a] - env[b], 1)
        for rule in self._of("corner_exit"):
            watched = self._corners.get(rule.id)
            if watched is not None and c.id not in watched:
                continue
            self._occur(rule, env, now, pushing=at_pace)

    def lap(self, info: dict, best_before: float | None, now: float) -> None:
        """A counted lap (see LiveCoach.on_lap for `info`)."""
        coach = self._coach
        env = {**self._env, "lap": coach._laps_done, "lap_time": info.get("lap_time"), "gap_s": info.get("gap_s"),
               "pace": info.get("pace"), "pushing_share": info.get("pushing_share"), "moment_at": info.get("moment_at")}
        lt = info.get("lap_time")
        env["best_gap_s"] = round(lt - best_before, 3) if lt and best_before else None
        env["new_best"] = bool(lt and (best_before is None or lt < best_before) and info.get("pace") == "pushing")
        worst = max(info.get("corners") or [], key=lambda r: r["delta_s"], default=None)
        env["worst_corner"] = worst["corner"] if worst else None
        env["worst_delta_s"] = worst["delta_s"] if worst else None
        env["worst_name"] = (coach.cmap.get(worst["corner"]).name or f"Turn {worst['corner']}") if worst else None
        for rule in self._of("lap"):
            self._occur(rule, env, now, pushing=info.get("pace") != "tranquille")
        for rule in self._of("every_laps"):
            if coach._laps_done % int(rule.when["every_laps"]) == 0:
                self._occur(rule, {**env, "count": self._state[rule.id].fired}, now)
        for rule in self._of("at_lap"):
            if coach._laps_done == int(rule.when["at_lap"]):
                self._occur(rule, env, now)

    def line_crossed(self) -> None:
        self._lap_no += 1

    def pace(self, mode: str, now: float) -> None:
        env = {**self._env, "mode": mode, "pushing": mode == "pushing"}
        for rule in self._of("pace"):
            if rule.when["pace"] in ("any", mode):
                self._occur(rule, env, now)

    # --- triggers ------------------------------------------------------------------------------

    def _of(self, trigger: str) -> list[Rule]:
        return [r for r in self.rules if r.trigger == trigger]

    def _base(self, frame, now: float) -> dict:
        coach = self._coach
        env: dict[str, Any] = dict(frame.values)
        speed = frame.get("Speed")
        focus_entry = next((f for f in reversed(coach.focus_log) if f["cue"] == coach.focus), None) if coach.focus else None
        env.update({
            "time": now, "since_start_s": now - self._start_s if self._start_s is not None else 0.0,
            "lap": coach._laps_done, "lap_dist": frame.get("LapDist"),
            "speed_kph": None if speed is None else speed * 3.6, "pushing": coach.mode == "pushing",
            "mode": coach.mode, "learning": coach._laps_done < coach.settings.learning_laps,
            "focus": coach.focus, "focus_label": focus_entry["label"] if focus_entry else None,
            "best_lap": coach._pace_lap, "ref_lap_time": coach.plan.ref_lap_time,
            "track": coach.session.track_key, "car": coach.session.car_key,
        })
        self._env = env
        return env

    def _position(self, rule: Rule, env: dict, now: float) -> None:
        st = self._state[rule.id]
        target, corner = self._at[rule.id]
        d, v = env.get("lap_dist"), max(env.get("Speed") or 0.0, 5.0)
        if d is None:
            return
        length = self._coach.length
        to_target = (target % length - d) % length
        lead = float(rule.when.get("lead_s", 0.8 if any(a.kind == "say" for a in rule.actions) else 0.0))
        if to_target > length / 2:
            # Behind us. Re-arm once clearly past, so jitter at the point can't fire it twice.
            if (d - target) % length >= REARM_PAST_M:
                st.armed = True
            return
        eta = to_target / v
        if not st.armed or eta > 30.0:
            return
        local = {**env, "target_m": round(target, 1), "to_target_m": round(to_target, 1)}
        if corner is not None:
            local.update({"corner": corner.id, "name": corner.name or f"Turn {corner.id}"})
        # A spoken cue starts so it finishes `lead_s` before the point.
        speak = 0.0
        for a in rule.actions:
            if a.kind == "say" and (text := a.template.render(local)):
                speak = max(speak, self._coach.arbiter.voice.duration(text) or Utterance(text, 0, "", 0, 0).length_s)
        if eta > speak + lead:
            return
        st.armed = False
        expires = now + max(0.0, eta - speak)
        self._occur(rule, local, now, cue_expires=expires)

    def _condition(self, rule: Rule, env: dict, now: float) -> None:
        st = self._state[rule.id]
        holds = bool(self._cond(rule)(env))
        for_s, rearm_s = float(rule.when.get("for_s", 0.0)), float(rule.when.get("rearm_s", 1.0))
        if holds:
            st.false_since = None
            st.true_since = now if st.true_since is None else st.true_since
            if st.armed and now - st.true_since >= for_s:
                st.armed = False
                self._occur(rule, env, now)
        else:
            st.true_since = None
            st.false_since = now if st.false_since is None else st.false_since
            if not st.armed and now - st.false_since >= rearm_s:
                st.armed = True

    def _cond(self, rule: Rule) -> Expr:
        expr = getattr(rule, "_trigger_expr", None)
        if expr is None:
            expr = Expr(str(rule.when["condition"]))
            rule._trigger_expr = expr  # type: ignore[attr-defined]
        return expr

    # --- firing --------------------------------------------------------------------------------

    def _occur(self, rule: Rule, env: dict, now: float, pushing: bool | None = None,
               cue_expires: float | None = None) -> None:
        stats, st = self.stats[rule.id], self._state[rule.id]
        stats.occurrences += 1
        if rule.pushing_only and rule.trigger in PUSHING_TRIGGERS:
            if not (env.get("pushing") if pushing is None else pushing):
                stats.skipped += 1
                return
        if rule.condition is not None and not rule.condition(env):
            st.streak = 0
            return
        st.streak += 1
        if st.streak < rule.in_a_row:
            return
        stats.matched += 1
        held = self._held(rule, st, now)
        record = {"rule": rule.id, "at": round(now, 2), "lap": env.get("lap"), "lap_no": self._lap_no,
                  "lap_dist": None if env.get("lap_dist") is None else round(env["lap_dist"]),
                  "trigger": rule.trigger, "values": self._values(rule, env)}
        if held:
            stats.limited += 1
            record.update({"result": "limited", "why": held})
            self._report(record)
            return
        st.streak = 0 if rule.in_a_row > 1 else st.streak
        st.fired += 1
        st.last_fired_s, st.last_fired_lap = now, self._lap_no
        st.per_lap[self._lap_no] = st.per_lap.get(self._lap_no, 0) + 1
        stats.fired += 1
        done = []
        for a in rule.actions:
            text = a.template.render(env)
            if text is None:
                done.append({a.kind: None, "missing": a.template.missing(env)})
                continue
            done.append({a.kind: text})
            if a.kind == "say":
                why = self._say(rule, a, text, env, now, cue_expires)
                if why:
                    done[-1]["held"] = why
            elif a.kind == "wake" and self.on_wake is not None:
                self.on_wake({"rule": rule.id, "message": text, "at": now, "values": record["values"],
                              "description": rule.description})
        record.update({"result": "fired", "actions": done})
        self._report(record)

    def _say(self, rule: Rule, a: Action, text: str, env: dict, now: float, cue_expires: float | None) -> str | None:
        priority = PRIORITIES[a.priority]
        lines = self._lines.get(self._lap_no, 0)
        if lines >= self.limits.max_lines_per_lap:
            return f"{lines} rule lines this lap already"
        if priority != APPROACH and now - self._last_line_s < self.limits.min_gap_s:
            return "another rule spoke just now"
        if a.expires_s is not None:
            expires = now + a.expires_s
        elif priority == APPROACH and cue_expires is not None:
            expires = cue_expires
        else:
            expires = now + (40.0 if rule.trigger not in ("at", "corner_exit", "condition") else 10.0)
        corner = env.get("corner") if isinstance(env.get("corner"), int) else None
        longer = a.longer.render(env) if a.longer is not None else None
        self._coach.arbiter.say(Utterance(text, priority, "rule", now, expires, corner, rule=rule.id, longer=longer))
        self._lines[self._lap_no] = lines + 1
        self._last_line_s = now
        return None

    def _held(self, rule: Rule, st: _State, now: float) -> str | None:
        lim = rule.limits
        if lim.get("once") and st.fired >= 1:
            return "once only"
        if "max_per_session" in lim and st.fired >= lim["max_per_session"]:
            return f"{st.fired} time(s) this session already"
        if "max_per_lap" in lim and st.per_lap.get(self._lap_no, 0) >= lim["max_per_lap"]:
            return "enough this lap"
        if "cooldown_s" in lim and st.last_fired_s is not None and now - st.last_fired_s < lim["cooldown_s"]:
            return f"cooldown ({lim['cooldown_s']:g} s)"
        if "cooldown_laps" in lim and st.last_fired_lap is not None and self._lap_no - st.last_fired_lap <= lim["cooldown_laps"]:
            return f"cooldown ({lim['cooldown_laps']:g} lap(s))"
        return None

    def _values(self, rule: Rule, env: dict) -> dict:
        """The values the rule looked at (for the log, backtests and the agent)."""
        names = set(rule.condition.names) if rule.condition else set()
        for a in rule.actions:
            names |= a.template.names | (a.longer.names if a.longer else set())
        if rule.trigger == "condition":
            names |= self._cond(rule).names
        out = {}
        for n in sorted(names):
            v = env.get(n)
            out[n] = round(v, 3) if isinstance(v, float) else v
        return out

    def _report(self, record: dict) -> None:
        self.firings.append(record)
        if self.on_fire is not None:
            self.on_fire(record)


def describe() -> dict:
    """Everything a rule can use, for `iagent rules vars` (and the agent)."""
    return {
        "triggers": {t: dict(TRIGGER_VARS[t]) for t in TRIGGERS},
        "every_trigger": dict(BASE_VARS),
        "channels": dict(CHANNELS),
        "functions": {k: v[1] for k, v in FUNCTIONS.items()},
        "priorities": list(PRIORITIES),
        "limits": ["cooldown_s", "cooldown_laps", "max_per_lap", "max_per_session", "once"],
    }
