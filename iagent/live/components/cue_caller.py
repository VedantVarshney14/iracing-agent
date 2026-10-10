"""The corner cues: which to say, and how, on each approach.

Every cue in the plan is watched. The first `learning_laps` laps cue every corner; after that the
focus (and corners in big trouble, or with a hint), or with no focus the corners that went badly,
so the coach goes quiet as the driver learns. A cue is said in full until it's been heard
`learning_laps` times, then as its short form; a new focus is said in full, with "Focus." first;
last lap's hint for the corner is added.

Adds `wanted` and `text` to the cue's `approach` events; saying it is a rule (`builtin_rules`).
"""

from iagent.live import phrasing
from iagent.live.events import define_fields, define_state
from iagent.live.pipeline import Component
from iagent.live.speech import Utterance

define_fields("approach", {"wanted": "a cue worth saying now", "text": "the cue as it would be said",
                           "corners": "the cue's corners", "full": "said in full (not the short form)"})
define_state(in_corner="between a cue's point and the end of its corners", cue_heard="cue corner -> times heard in full",
             focus_heard="the current focus has been cued since it was set")


class CueCaller(Component):
    def start(self):
        self.state.update(in_corner=False, cue_heard={}, focus_heard=True)
        for cue in self.ctx.plan.cues:
            self.emit("watch", id=f"cue:{cue.corner}", target_m=cue.target_m, lead_s=self.settings.lead_s,
                      speak=lambda cue=cue: self._speak(cue),
                      tags={"source": "cue", "corner": cue.corner, "corners": cue.corners,
                            "name": phrasing.corner_name(self.ctx.cmap.get(cue.corner))})

    def wanted(self, cue) -> bool:
        st = self.state
        if not st["pushing"]:
            return False
        if st["learning"]:
            return True
        hints, focus = st["hints"], st.get("focus")
        if focus is not None:
            return cue.corner == focus or any(c in st["big_trouble"] or c in hints for c in cue.corners)
        return any(c in st["struggling"] or c in hints for c in cue.corners)

    def full(self, cue) -> bool:
        if not (self.settings.short_cues and cue.short):
            return True
        if cue.corner == self.state.get("focus") and not self.state["focus_heard"]:
            return True  # a new focus is said in full
        return self.state["cue_heard"].get(cue.corner, 0) < max(1, self.settings.learning_laps)

    def text(self, cue) -> str:
        hints = self.state["hints"]
        corner = next((c for c in cue.corners if c in hints), None)
        second = phrasing.corner_name(self.ctx.cmap.get(corner)) if corner not in (None, cue.corner) else None
        text = phrasing.with_hint(cue.text if self.full(cue) else cue.short, hints.get(corner), second)
        if cue.corner == self.state.get("focus") and not self.state["focus_heard"]:
            return f"Focus. {text}"
        return text

    def _speak(self, cue) -> float | None:
        if not self.wanted(cue):
            return None
        text = self.text(cue)
        return self.ctx.arbiter.voice.duration(text) or Utterance(text, 0, "", 0, 0).length_s

    def enrich_approach(self, e):
        cue = self.ctx.plan.cue_for(e["corner"]) if e.get("source") == "cue" else None
        if cue is None:
            return
        e.fields.update(wanted=self.wanted(cue), text=self.text(cue), full=self.full(cue))

    def on_approach(self, e):
        if not e.get("wanted") or e.get("source") != "cue":
            return
        cue, st = self.ctx.plan.cue_for(e["corner"]), self.state
        if e["full"]:
            st["cue_heard"][cue.corner] = st["cue_heard"].get(cue.corner, 0) + 1
        if cue.corner == st.get("focus"):
            st["focus_heard"] = True
        for c in cue.corners:
            st["hints"].pop(c, None)

    def on_frame(self, e):
        d = self.state["lap_dist"]
        if d is None:
            return
        length = self.ctx.plan.length_m
        inside = False
        for cue in self.ctx.plan.cues:
            end = self.ctx.cmap.get(cue.corners[-1]).exit_m
            start = cue.target_m - 100.0
            if start <= d <= end or (start < 0 and d >= start % length):
                inside = True
                break
        self.state["in_corner"] = inside
