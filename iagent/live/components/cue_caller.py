"""The corner cues: which to say, and how, on each approach.

Every cue in the plan is watched. The first `learning_laps` laps cue every corner; after that the
focus (and corners in big trouble, or with a hint), or with no focus the corners that went badly,
so the coach goes quiet as the driver learns. A cue is said in full until it's been heard
`learning_laps` times, then as its short form; a new focus is said in full, with "Focus." first;
last lap's hint for the corner is added. Sets `wanted` and `text` on the cue's `Approach`; saying
it is a rule (`builtin_rules`).
"""

from iagent.live import phrasing
from iagent.live.events import Approach, Frame, Watch
from iagent.live.pipeline import Component, enrich, on
from iagent.live.speech import Utterance


class CueCaller(Component):
    def start(self):
        for cue in self.ctx.plan.cues:
            self.emit(Watch(id=f"cue:{cue.corner}", target_m=cue.target_m, lead_s=self.settings.lead_s,
                            speak=lambda cue=cue: self._speak(cue),
                            tags={"source": "cue", "corner": cue.corner, "corners": cue.corners,
                                  "corner_name": phrasing.corner_name(self.ctx.cmap.get(cue.corner))}))

    def wanted(self, cue) -> bool:
        st = self.state
        if not st.pushing:
            return False
        if st.learning:
            return True
        if st.focus is not None:
            return cue.corner == st.focus or any(c in st.big_trouble or c in st.hints for c in cue.corners)
        return any(c in st.struggling or c in st.hints for c in cue.corners)

    def full(self, cue) -> bool:
        st = self.state
        if not (self.settings.short_cues and cue.short):
            return True
        if cue.corner == st.focus and not st.focus_heard:
            return True  # a new focus is said in full
        return st.cue_heard.get(cue.corner, 0) < max(1, self.settings.learning_laps)

    def text(self, cue) -> str:
        st = self.state
        corner = next((c for c in cue.corners if c in st.hints), None)
        second = phrasing.corner_name(self.ctx.cmap.get(corner)) if corner not in (None, cue.corner) else None
        text = phrasing.with_hint(cue.text if self.full(cue) else cue.short, st.hints.get(corner), second)
        return f"Focus. {text}" if cue.corner == st.focus and not st.focus_heard else text

    def _speak(self, cue) -> float | None:
        if not self.wanted(cue):
            return None
        text = self.text(cue)
        return self.ctx.arbiter.voice.duration(text) or Utterance(text, 0, "", 0, 0).length_s

    @enrich(Approach)
    def words(self, e: Approach):
        cue = self.ctx.plan.cue_for(e.corner) if e.source == "cue" else None
        if cue is not None:
            e.wanted, e.text, e.full = self.wanted(cue), self.text(cue), self.full(cue)

    @on(Approach)
    def heard(self, e: Approach):
        if e.source != "cue" or not e.wanted:
            return
        cue, st = self.ctx.plan.cue_for(e.corner), self.state
        if e.full:
            st.cue_heard[cue.corner] = st.cue_heard.get(cue.corner, 0) + 1
        if cue.corner == st.focus:
            st.focus_heard = True
        for c in cue.corners:
            st.hints.pop(c, None)

    @on(Frame)
    def inside(self, e: Frame):
        d = self.state.lap_dist
        if d is None:
            return
        length = self.ctx.plan.length_m
        self.state.in_corner = any(
            (cue.target_m - 100.0 <= d <= self.ctx.cmap.get(cue.corners[-1]).exit_m)
            or (cue.target_m - 100.0 < 0 and d >= (cue.target_m - 100.0) % length)
            for cue in self.ctx.plan.cues)
