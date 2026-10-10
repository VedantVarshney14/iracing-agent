"""What the coach remembers about each corner, and the feedback line for a corner exit.

From the corners driven at pace: which the driver struggles with (cued again after the learning
laps), which went badly enough to speak up even with a focus set, the hint for each corner's cue
next lap, and what was said about it last time, so a repeat is said as a repeat and a fixed
corner gets a "better".

Adds `feedback` (and `feedback_long`) to `corner_exit`: what a coach would say about it now, or
nothing. Saying it is a rule (`builtin_rules`).
"""

from iagent.live import phrasing
from iagent.live.events import define_event, define_fields, define_state
from iagent.live.pipeline import Component

define_fields("corner_exit", {
    "feedback": "what to say about the corner now (aware of last time), or none",
    "feedback_long": "the same with the why and the how",
    "in_focus": "it's (part of) the focus",
})
define_event("advice", "Advice for a corner: said after it, or as a hint in its cue next lap.", {
    "corner": ("corner", "the corner"), "lap": "the lap it was about", "delta_s": "time lost",
    "advice": "said after the corner, or none", "hint": "for its cue next lap, or none",
}, log=True)
define_state(
    struggling="corners that went badly last time at pace (a set)",
    big_trouble="corners worth a word even with a focus set (a set)",
    hints="corner -> hint for its cue next lap",
)


class History(Component):
    def start(self):
        self.state.update(struggling=set(), big_trouble=set(), hints={})
        self._told: dict[int, tuple[str, int]] = {}  # corner -> (cause, lap) of the last feedback said
        self._advised: set[int] = set()  # corners with advice this lap

    def enrich_corner_exit(self, e):
        s, st = self.settings, self.state
        c, cue = e["corner"], self.ctx.plan.cue_for(e["corner"])
        e.fields.update(feedback=None, feedback_long=None,
                        in_focus=cue is not None and cue.corner == st.get("focus"))
        if not e["at_pace"]:
            # Not pushing: the numbers don't count, but going off is still worth a word next lap.
            if e["hint"] and e["cause"] == "off":
                st["hints"][c] = e["hint"]
            return
        (st["struggling"].add if e["struggling"] else st["struggling"].discard)(c)
        trouble = e["delta_s"] >= s.others_s or e["cause"] == "off"
        (st["big_trouble"].add if trouble else st["big_trouble"].discard)(c)
        if st.get("focus") is not None and not e["in_focus"] and c not in st["big_trouble"]:
            return  # with a focus set, other corners keep quiet unless it was bad
        if e["hint"]:
            st["hints"][c] = e["hint"]
        if e["advice"] or e["hint"]:
            self.emit("advice", corner=c, lap=st["lap"] + 1, delta_s=e["delta_s"], advice=e["advice"], hint=e["hint"])
        said = cue is not None and any(x in self._advised for x in cue.corners)
        if e["advice"]:
            self._advised.add(c)
        if said:
            return  # one word per cue a lap (a chicane's second corner waits)
        line = self._line(e)
        if line:
            e["feedback"], e["feedback_long"] = line

    def _line(self, e) -> tuple[str, str | None] | None:
        lap = self.state["lap"] + 1
        c, told = e["corner"], self._told.get(e["corner"])
        recent = told is not None and lap - told[1] <= 1
        if e["advice"]:
            self._told[c] = (e["cause"], lap)
            if recent and told[0] == e["cause"] and e["cause"] not in (None, "off"):
                return phrasing.again(e["name"], e["advice"]), e["longer"]
            return e["advice"], e["longer"]
        if recent and e["delta_s"] < self.settings.loss_s:
            del self._told[c]
            return phrasing.better(e["name"])
        return None

    def on_crossing(self, e):
        self._advised = set()
