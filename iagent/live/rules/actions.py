"""What a rule can do when it fires: subclasses of `Action`.

An action is parsed once, from the rule's JSON (`parse`), and run each time the rule fires
(`run`), with the engine (to speak, emit events, read state), the rule, the rendered text, the
variables the rule saw and the event. A new action is a new subclass registered in `ACTIONS`.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from iagent.live.events import Approach, CoachWords, Event, Narrate, Wake
from iagent.live.expr import Expr, ExprError, Template
from iagent.live.speech import APPROACH, FEEDBACK, SUMMARY, Utterance

if TYPE_CHECKING:
    from iagent.live.rules.engine import RuleEngine
    from iagent.live.rules.schema import Rule

PRIORITIES = {"cue": APPROACH, "feedback": FEEDBACK, "answer": FEEDBACK, "summary": SUMMARY}
WAKE_MIN_GAP_S = 30.0  # wake-ups at most this often (each is a model turn)


class RuleError(ValueError):
    pass


def template(rule_id: str, text: Any, names: set[str]) -> Template:
    try:
        t = Template(str(text))
    except ExprError as e:
        raise RuleError(f"{rule_id}: {e}") from None
    check_names(rule_id, t.names, names)
    return t


def expression(rule_id: str, text: Any, names: set[str]) -> Expr:
    try:
        expr = Expr(str(text))
    except ExprError as e:
        raise RuleError(f"{rule_id}: {e}") from None
    check_names(rule_id, expr.names, names)
    return expr


def check_names(rule_id: str, used: set[str], names: set[str]) -> None:
    unknown = sorted(used - names)
    if unknown:
        raise RuleError(f"{rule_id}: unknown variable(s) {', '.join(unknown)}. See `iagent rules vars` for what each "
                        "event provides.")


class Action(ABC):
    """One thing a rule does. `key` is its name in the rule's JSON ({"say": "..."})."""

    key: str = ""
    doc: str = ""
    options: tuple[str, ...] = ()  # the fields it takes besides its own key

    def __init__(self, rule_id: str, raw: dict, event: str, names: set[str]):
        extra = set(raw) - {self.key} - set(self.options)
        if extra:
            raise RuleError(f"{rule_id}: `{self.key}` doesn't take {', '.join(sorted(extra))}.")
        self.template = template(rule_id, raw[self.key], names)
        if not self.template.text.strip():
            raise RuleError(f"{rule_id}: `{self.key}` is empty.")
        self.raw = {k: raw[k] for k in self.options if k in raw}

    def names(self) -> set[str]:
        """The variables it reads."""
        return set(self.template.names)

    def to_dict(self) -> dict:
        return {self.key: self.template.text, **self.raw}

    @abstractmethod
    def run(self, engine: "RuleEngine", rule: "Rule", text: str, env: dict, e: Event) -> dict:
        """Do it; returns anything worth recording about what happened (e.g. why it was held)."""


class Say(Action):
    key, doc = "say", "say it (cue, feedback or summary priority; `long` for when there's time)"
    options = ("long", "priority", "kind", "expires_s")

    def __init__(self, rule_id, raw, event, names):
        super().__init__(rule_id, raw, event, names)
        self.priority = raw.get("priority", "cue" if event == Approach.name else "summary" if event == "lap" else "feedback")
        if self.priority not in PRIORITIES:
            raise RuleError(f"{rule_id}: priority is one of {', '.join(PRIORITIES)}.")
        expires = raw.get("expires_s")
        if isinstance(expires, str):
            expires = expression(rule_id, expires, names)
        elif expires is not None and (isinstance(expires, bool) or not isinstance(expires, (int, float)) or expires <= 0):
            raise RuleError(f"{rule_id}: expires_s is a number of seconds > 0 (or an expression).")
        self.expires = expires
        self.longer = template(rule_id, raw["long"], names) if raw.get("long") else None
        self.kind = template(rule_id, raw.get("kind", "rule"), names)

    def names(self) -> set[str]:
        return super().names() | (self.longer.names if self.longer else set()) | self.kind.names

    def run(self, engine, rule, text, env, e):
        priority = PRIORITIES[self.priority]
        held = engine.group_held(rule, priority, e.at)
        if held:
            return {"held": held}
        expires = self.expires(env) if isinstance(self.expires, Expr) else self.expires
        if expires is not None:
            until = e.at + float(expires)
        elif priority == APPROACH and getattr(e, "expires_at", None) is not None:
            until = e.expires_at  # a cue that can't finish before its point is no use
        else:
            until = e.at + (10.0 if e.judged else 40.0)
        corner = env.get("corner") if isinstance(env.get("corner"), int) else None
        longer = self.longer.render(env) if self.longer else None
        by = getattr(e, "rule", None) or rule.id  # a reply to a rule's wake-up is that rule's line
        engine.ctx.arbiter.say(Utterance(text, priority, self.kind.render(env) or "rule", e.at, until, corner, rule=by,
                                         longer=longer))
        engine.said(rule, priority, e.at)
        return {}


class WakeEngineer(Action):
    key, doc = "wake", "wake the engineer: it may answer on the radio in its own words"

    def run(self, engine, rule, text, env, e):
        st = engine.state
        wake = {"rule": rule.id, "message": text, "values": engine.values(rule, env), "description": rule.description}
        engine.emit(Wake(**wake))
        engine.emit(Narrate(kind="wake", reply=CoachWords.name, rule=rule.id, min_gap_s=WAKE_MIN_GAP_S, payload={
            "wake": wake, "state": {"mode": st.mode, "lap": st.lap, "focus_label": st.focus_label,
                                    "crewchief": engine.settings.crewchief}}))
        return {}


class Log(Action):
    key, doc = "log", "note it in the session log"

    def run(self, engine, rule, text, env, e):
        return {}


ACTIONS: dict[str, type[Action]] = {a.key: a for a in (Say, WakeEngineer, Log)}


def parse_action(rule_id: str, raw: Any, event: str, names: set[str]) -> Action:
    if not isinstance(raw, dict):
        raise RuleError(f"{rule_id}: an action is an object like {{\"say\": \"...\"}}.")
    keys = [k for k in ACTIONS if k in raw]
    if len(keys) != 1:
        raise RuleError(f"{rule_id}: each action is one of {', '.join(ACTIONS)}.")
    return ACTIONS[keys[0]](rule_id, raw, event, names)
