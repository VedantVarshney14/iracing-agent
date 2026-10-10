"""The rule engine: a pipeline component that sees every event and fires the rules for it.

The coach's own behaviour is the built-in rules (`iagent.live.builtin_rules`, group `coach`); the
agent's rules come from the context (`ctx.rules`) and can be replaced mid-session. Groups set limits
for a whole set of rules: the agent's share a budget of lines a lap, the coach's own don't.
"""

import logging
from dataclasses import dataclass, field

from iagent.live.events import Event, RuleFired, Unwatch, Watch
from iagent.live.pipeline import Component, on
from iagent.live.rules.actions import Say
from iagent.live.rules.schema import Rule, resolve_at, resolve_filters
from iagent.live.speech import APPROACH, Utterance

logger = logging.getLogger("iagent.live")


@dataclass(frozen=True)
class Group:
    max_lines_per_lap: int | None = None  # lines said a lap, all the group's rules together
    min_gap_s: float = 0.0  # between two of the group's lines (cues aside)
    report: bool = True  # firings go in the session log as `rule` events


GROUPS = {"agent": Group(max_lines_per_lap=6, min_gap_s=4.0), "coach": Group(report=False)}


@dataclass
class RuleStats:
    occurrences: int = 0  # the event happened (and passed the filters)
    skipped: int = 0  # ... while not pushing (pushing_only)
    matched: int = 0  # ... and the condition held (in_a_row times)
    fired: int = 0
    limited: int = 0  # matched, but a limit held it back


@dataclass
class _RuleState:
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
    lines: dict[int, int] = field(default_factory=dict)  # line crossings -> lines said
    last_line_s: float = -1e9


@dataclass
class _Compiled:
    rule: Rule
    filters: dict[str, set | None]


class RuleEngine(Component):
    def start(self):
        from iagent.live.builtin_rules import builtin_rules

        self.stats: dict[str, RuleStats] = {}
        self.firings: list[dict] = []
        self.errors: list[str] = []
        self._st: dict[str, _RuleState] = {}
        self._groups = {g: _GroupState() for g in GROUPS}
        self._builtin = [Rule.from_dict(r) for r in builtin_rules(self.settings)]
        self.rules: list[Rule] = []  # the agent's, as live
        self._by_event: dict[str, list[_Compiled]] = {}
        self._watches: set[str] = set()
        self.replace(list(self.ctx.rules or []))

    def replace(self, rules: list[Rule]) -> None:
        """Swap in the agent's rules (e.g. one activated mid-session); counters carry over."""
        session = self.ctx.session
        for watch in self._watches:
            self.emit(Unwatch(id=watch))
        self._watches = set()
        by_event: dict[str, list[_Compiled]] = {}
        kept, errors = [], []
        for rule in [*self._builtin, *rules]:
            if not rule.applies_to(session.track_key, session.car_key):
                continue
            if rule.enabled_by and not getattr(self.settings, rule.enabled_by):
                continue
            try:
                filters = resolve_filters(rule, self.ctx.cmap)
                if "at" in rule.match:
                    filters["watch"] = {self._watch(rule)}
            except KeyError as e:
                errors.append(f"{rule.id}: {e.args[0]}")
                continue
            by_event.setdefault(rule.trigger, []).append(_Compiled(rule, filters))
            self.stats.setdefault(rule.id, RuleStats())
            self._st.setdefault(rule.id, _RuleState())
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
        tags = {"source": "rule"}
        if corner is not None:
            tags.update(corner=corner.id, corner_name=corner.name or f"Turn {corner.id}")
        says = [a for a in rule.actions if isinstance(a, Say)]
        lead = float(rule.match.get("lead_s", 0.8 if says else 0.0))

        def speak() -> float:
            env = {**self.pipe.channels, **self.state.variables(), "target_m": target, "corner": tags.get("corner"),
                   "name": tags.get("corner_name")}
            texts = [t for a in says if (t := a.template.render(env))]
            voice = self.ctx.arbiter.voice
            return max((voice.duration(t) or Utterance(t, 0, "", 0, 0).length_s for t in texts), default=0.0)
        watch = f"rule:{rule.id}"
        self.emit(Watch(id=watch, target_m=target, lead_s=lead, speak=speak, tags=tags))
        self._watches.add(watch)
        return watch

    # --- events --------------------------------------------------------------------------------

    @on(Event)
    def evaluate(self, e: Event):
        rules = self._by_event.get(e.name)
        if not rules:
            return
        env = None
        for c in rules:
            if env is None:
                env = {**self.pipe.channels, **self.state.variables(), **e.variables()}
            if any(allowed is not None and env.get(key) not in allowed for key, allowed in c.filters.items()):
                continue
            if c.rule.where is not None and not c.rule.where(env):
                continue
            if c.rule.edge is not None and not self._edge(c.rule, env, e.at):
                continue
            self._occur(c.rule, env, e)

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

    def _occur(self, rule: Rule, env: dict, e: Event) -> None:
        stats, st = self.stats[rule.id], self._st[rule.id]
        stats.occurrences += 1
        if rule.pushing_only and e.judged and not env.get("pushing"):
            stats.skipped += 1
            return
        if rule.condition is not None and not rule.condition(env):
            st.streak = 0
            return
        st.streak += 1
        if st.streak < rule.in_a_row:
            return
        stats.matched += 1
        lap_no = self.state.laps_driven
        record = {"rule": rule.id, "lap": self.state.lap, "lap_no": lap_no, "trigger": rule.trigger,
                  "lap_dist": None if env.get("lap_dist") is None else round(env["lap_dist"]),
                  "values": self.values(rule, env)}
        held = self._held(rule, st, e.at)
        if held:
            stats.limited += 1
            self._report(rule, {**record, "result": "limited", "why": held}, e.at)
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
                done.append({a.key: None, "missing": a.template.missing(env)})
                continue
            done.append({a.key: text, **(a.run(self, rule, text, env, e) or {})})
        self._report(rule, {**record, "result": "fired", "actions": done}, e.at)

    # --- limits --------------------------------------------------------------------------------

    def group_held(self, rule: Rule, priority: int, now: float) -> str | None:
        g, gs = GROUPS[rule.group], self._groups[rule.group]
        lines = gs.lines.get(self.state.laps_driven, 0)
        if g.max_lines_per_lap is not None and lines >= g.max_lines_per_lap:
            return f"{lines} rule lines this lap already"
        if priority != APPROACH and now - gs.last_line_s < g.min_gap_s:
            return "another rule spoke just now"
        return None

    def said(self, rule: Rule, priority: int, now: float) -> None:
        gs, lap_no = self._groups[rule.group], self.state.laps_driven
        gs.lines[lap_no] = gs.lines.get(lap_no, 0) + 1
        gs.last_line_s = now

    def _held(self, rule: Rule, st: _RuleState, now: float) -> str | None:
        lim, lap_no = rule.limits, self.state.laps_driven
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
        out = {}
        for n in sorted(rule.names()):
            v = env.get(n)
            if not isinstance(v, (set, dict, list)):
                out[n] = round(v, 3) if isinstance(v, float) else v
        return out

    def _report(self, rule: Rule, record: dict, at: float) -> None:
        self.firings.append({**record, "at": round(at, 2)})
        if GROUPS[rule.group].report:
            self.emit(RuleFired(**record))
