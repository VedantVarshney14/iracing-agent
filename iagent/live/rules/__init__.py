"""Rules: what to do when an event happens. Everything the coach says goes through them.

A rule is data: an event (`when`), an optional condition (`if`, see `iagent.live.expr`), one or
more actions and limits. The coach's own behaviour (corner cues, feedback, the lap summary, the
focus news, the debrief) is a set of built-in rules (`iagent.live.builtin_rules`); the agent adds
its own through the CLI (`iagent.live.rulebook`).

- `schema`: the rule format, a JSON Schema generated from the events (`rule_schema`), and `Rule`;
- `actions`: what a rule does when it fires (`Say`, `WakeEngineer`, `Log`: subclasses of `Action`);
- `engine`: the pipeline component that evaluates them (`RuleEngine`), with limits and groups.
"""

from iagent.live.events import EVENTS
from iagent.live.expr import FUNCTIONS
from iagent.live.rules.actions import ACTIONS, PRIORITIES, Action, RuleError
from iagent.live.rules.engine import GROUPS, RuleEngine, RuleStats
from iagent.live.rules.schema import CHANNELS, EXTRAS, LIMITS, Rule, check_against_map, rule_schema, variables
from iagent.live.state import CoachState

__all__ = ["ACTIONS", "Action", "CHANNELS", "GROUPS", "PRIORITIES", "Rule", "RuleEngine", "RuleError", "RuleStats",
           "check_against_map", "describe", "rule_schema", "variables"]


def describe() -> dict:
    """Everything a rule can use, for `iagent rules vars` (and the agent): from the definitions."""
    return {
        "events": {name: {"doc": (cls.__doc__ or "").strip(), "fields": cls.variable_docs(), "judged": cls.judged,
                          "extras": sorted(EXTRAS.get(name, {}))} for name, cls in sorted(EVENTS.items())},
        "state": CoachState.docs(),
        "channels": dict(CHANNELS),
        "functions": {k: v[1] for k, v in FUNCTIONS.items()},
        "actions": {k: v.doc for k, v in ACTIONS.items()},
        "priorities": list(PRIORITIES),
        "limits": list(LIMITS),
    }
