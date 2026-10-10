"""The cool-down debrief: what an engineer says when the driver slows down.

Where the time is over the last laps at pace (a habit, not one mistake), what to change and how,
and how consistent the laps were. As the driver slows (`slowing`) the coach's own words are asked
for (`narrate`); at `cool_down` they're said if they've come, else after up to `narrate_wait_s`
more, else the template's. Each piece is a `debrief_line`; saying it is a rule.
"""

from iagent.live import phrasing
from iagent.live.events import define_event, define_state
from iagent.live.narrator import chunks
from iagent.live.pipeline import Component

define_event("debrief", "The debrief was given.", {"by": '"coach" (its own words) or "template"', "text": "all of it"},
             log=True)
define_event("debrief_line", "A piece of the debrief, to say.", {"text": "the words", "index": "which piece"})
define_event("debrief_words", "The coach's words for a debrief arrived.", {
    "text": "the words, or none", "stretch": "the slow stretch it was for", "asked_at": "when it was asked for",
    "status": '"used", "too late" or "none"'}, log=True)
define_state(debriefs="debriefs given this session")

CAUSE_MEANINGS = {c.name: c.doc for c in phrasing.CAUSES}


class Debrief(Component):
    def start(self):
        self.state["debriefs"] = 0
        self.results: list[list] = []  # per counted lap: the corners at pace
        self._reset(None)

    def _reset(self, stretch):
        self._stretch, self._words, self._waiting, self._due_at, self._done = stretch, None, False, None, False

    def on_lap(self, e):
        self.results.append(e["results"])

    def on_pace(self, e):
        self._reset(self.state["stretch"] if e["mode"] == "tranquille" else None)

    def on_slowing(self, e):
        if not self.settings.debrief:
            return
        facts = self.facts()
        if facts["topics"] or facts["lap_times"]:
            self.emit("narrate", kind="debrief", facts=facts, reply="debrief_words", stretch=e["stretch"])

    def on_narration_asked(self, e):
        if e["reply"] == "debrief_words" and e["stretch"] == self._stretch:
            self._waiting = True

    def enrich_debrief_words(self, e):
        fits = e["stretch"] == self._stretch and self.state["mode"] == "tranquille" and not self._done
        e["status"] = "none" if not e["text"] else "used" if fits else "too late"
        if e["text"] and fits:
            self._words = e["text"]
        elif e["stretch"] == self._stretch:
            self._waiting = False  # nothing came: don't wait for it

    def on_cool_down(self, e):
        if self.settings.debrief and e["stretch"] == self._stretch:
            self._due_at = e.at

    def on_frame(self, e):
        if self._due_at is None or self._done:
            return
        waiting = self._waiting and self._words is None and e.at < self._due_at + self.settings.narrate_wait_s
        if waiting:
            return
        self._done = True
        lines = chunks(self._words) if self._words else self.lines()
        for i, text in enumerate(lines):
            self.emit("debrief_line", text=text, index=i)
        if lines:
            self.emit("debrief", by="coach" if self._words else "template", text=" ".join(lines))
        self.state["debriefs"] += 1

    def topics(self) -> list[dict]:
        """The focus, then the biggest losses over the last laps at pace."""
        s, st = self.settings, self.state
        by_corner: dict[int, list] = {}
        for lap in [lap for lap in self.results if lap][-3:]:
            for r in lap:
                if r.delta_s <= s.incident_s:
                    by_corner.setdefault(r.corner, []).append(r)
        mean = {c: sum(r.delta_s for r in rs) / len(rs) for c, rs in by_corner.items()}
        focus = st["focus_corners"]
        order = sorted(mean, key=lambda c: (c not in focus, -mean[c]))
        ref = {m["corner"]: m for m in self.ctx.plan.ref_metrics}
        out = []
        for corner in [c for c in order if mean[c] >= s.loss_s][: s.debrief_topics]:
            rs = by_corner[corner]
            latest = next((r for r in reversed(rs) if r.cause), None)
            out.append({"corner": corner, "name": phrasing.corner_name(self.ctx.cmap.get(corner)),
                        "in_focus": corner in focus, "loss_per_lap_s": round(mean[corner], 2), "laps": len(rs),
                        "cause": latest.cause if latest else None,
                        "amount": round(latest.amount, 1) if latest and latest.amount is not None else None,
                        "advice": latest.longer if latest else None,
                        "ref": ref.get(corner) or {}, "mine": (latest.metrics or {}) if latest else {}})
        return out

    def lines(self) -> list[str]:
        if not self.settings.debrief or not [lap for lap in self.results if lap]:
            return []
        return phrasing.debrief_lines(self.topics(), self.state["lap_times"], self.state["debriefs"])

    def facts(self) -> dict:
        """What the debrief is about, for the narrator."""
        times = self.state["lap_times"][-3:]
        topics = [{k: v for k, v in t.items() if k not in ("ref", "mine")} for t in self.topics()]
        return {"track": self.ctx.session.track_name, "laps_done": self.state["lap"], "focus": self.state["focus_label"],
                "topics": topics, "lap_times": [round(t, 2) for t in times],
                "lap_spread_s": round(max(times) - min(times), 2) if len(times) >= 2 else None,
                "cause_meanings": CAUSE_MEANINGS, "crewchief": self.settings.crewchief, "fallback": self.lines()}
