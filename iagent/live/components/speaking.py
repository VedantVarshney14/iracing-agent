"""When to speak: the speech arbiter, ticked after each frame, held back by `GATES`.

A gate is a condition over the shared state and what it does while it holds: hold everything,
let only cues through (no room for anything else), allow the longer versions of lines, or clear
lines of some kinds from the queue (while it holds, or as it starts). A new reason to keep quiet
is a new gate here, nothing else.
"""

from dataclasses import dataclass

from iagent.live.events import FrameDone, Line
from iagent.live.expr import Expr
from iagent.live.pipeline import Component, on


@dataclass(frozen=True)
class Gate:
    name: str
    when: str  # an expression over the shared state (iagent.live.expr)
    hold: bool = False  # nothing new is started
    cues_only: bool = False  # only corner cues (no room for anything else)
    long_ok: bool = False  # the driver can take a line's longer version
    clear: tuple[str, ...] = ()  # line kinds dropped while it holds
    clear_on_enter: tuple[str, ...] = ()  # line kinds dropped as it starts
    why: str = ""  # said in the log for a line it held back or dropped


GATES = [
    Gate("off track", "not on_track", hold=True, clear=("approach", "feedback"),
         why="you were in the pits or off the racing surface"),
    Gate("car alongside", "alongside", hold=True, why="a car was alongside"),
    Gate("not pushing", "mode == 'tranquille'", long_ok=True, clear_on_enter=("approach", "feedback", "focus"),
         why="you weren't pushing"),
    Gate("settling", "mode == 'tranquille' and not settled", hold=True, why="you'd just slowed down"),
    Gate("after the line", "after_line", cues_only=True, why="CrewChief reads the lap time then"),
    Gate("in a corner", "in_corner and pushing", cues_only=True, why="in a corner"),
]

class Speaking(Component):
    def start(self):
        self._gates = [(g, Expr(g.when)) for g in GATES]
        self._active: set[str] = set()
        arbiter = self.ctx.arbiter
        arbiter.on_event = lambda what, u, now: self.emit(Line(
            status=what, kind=u.kind, text=u.text, corner=u.corner, rule=u.rule,
            note=u.note if what == "dropped" else None))

    @on(FrameDone)
    def speak(self, e: FrameDone):
        arbiter, now = self.ctx.arbiter, e.at
        hold = cues_only = long_ok = False
        why = ""
        active = set()
        state = self.state.variables()
        for gate, expr in self._gates:
            if not expr(state):
                continue
            active.add(gate.name)
            if gate.clear:
                arbiter.clear(gate.clear, now, gate.why)
            if gate.clear_on_enter and gate.name not in self._active:
                arbiter.clear(gate.clear_on_enter, now, gate.why)
            if gate.hold and not hold:
                hold, why = True, gate.why
            cues_only |= gate.cues_only
            long_ok |= gate.long_ok
        self._active = active
        free = 0.0 if cues_only else self.state.next_cue_s
        arbiter.tick(now, hold=hold, free_for_s=free, long_ok=long_ok, hold_why=why)
