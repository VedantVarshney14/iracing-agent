"""What the coach remembers about each corner, and the feedback line for a corner exit.

From the corners driven at pace: which the driver struggles with (cued again after the learning
laps), which went badly enough to speak up even with a focus set, the hint for each corner's cue
next lap, and what was said about it last time, so a repeat is said as a repeat and a fixed
corner gets a "better". Sets `feedback` (and `feedback_long`) on `CornerExit`: what a coach would
say about it now, or nothing. Saying it is a rule (`builtin_rules`).
"""

from iagent.live import phrasing
from iagent.live.events import Advice, CornerExit, Crossing
from iagent.live.pipeline import Component, enrich, on


class History(Component):
    def start(self):
        self._told: dict[int, tuple[str, int]] = {}  # corner -> (cause, lap) of the last feedback said
        self._advised: set[int] = set()  # corners with advice this lap

    @enrich(CornerExit)
    def feedback(self, e: CornerExit):
        s, st = self.settings, self.state
        cue = self.ctx.plan.cue_for(e.corner)
        e.in_focus = cue is not None and cue.corner == st.focus
        if not e.at_pace:
            # Not pushing: the numbers don't count, but going off is still worth a word next lap.
            if e.hint and e.cause == "off":
                st.hints[e.corner] = e.hint
            return
        (st.struggling.add if e.struggling else st.struggling.discard)(e.corner)
        trouble = e.delta_s >= s.others_s or e.cause == "off"
        (st.big_trouble.add if trouble else st.big_trouble.discard)(e.corner)
        if st.focus is not None and not e.in_focus and e.corner not in st.big_trouble:
            return  # with a focus set, other corners keep quiet unless it was bad
        if e.hint:
            st.hints[e.corner] = e.hint
        if e.advice or e.hint:
            self.emit(Advice(corner=e.corner, lap=st.lap + 1, delta_s=e.delta_s, advice=e.advice, hint=e.hint))
        said = cue is not None and any(c in self._advised for c in cue.corners)
        if e.advice:
            self._advised.add(e.corner)
        if not said:  # one word per cue a lap (a chicane's second corner waits)
            e.feedback, e.feedback_long = self.line(e) or (None, None)

    def line(self, e: CornerExit) -> tuple[str, str | None] | None:
        lap = self.state.lap + 1
        told = self._told.get(e.corner)
        recent = told is not None and lap - told[1] <= 1
        if e.advice:
            self._told[e.corner] = (e.cause, lap)
            if recent and told[0] == e.cause and e.cause not in (None, "off"):
                return phrasing.again(e.corner_name, e.advice), e.longer
            return e.advice, e.longer
        if recent and e.delta_s < self.settings.loss_s:
            del self._told[e.corner]
            return phrasing.better(e.corner_name)
        return None

    @on(Crossing)
    def new_lap(self, e: Crossing):
        self._advised = set()
