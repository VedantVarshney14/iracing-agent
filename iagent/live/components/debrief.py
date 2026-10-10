"""The cool-down debrief: what an engineer says when the driver slows down.

Where the time is over the last laps at pace (a habit, not one mistake), what to change and how,
and how consistent the laps were. As the driver slows (`slowing`) the coach's own words are asked
for (`narrate`); at `cool_down` they're said if they've come, else after up to `narrate_wait_s`
more, else the template's. Each piece is a `DebriefLine`; saying it is a rule.
"""

from iagent.live import phrasing
from iagent.live.events import (CoolDown, DebriefGiven, DebriefLine, DebriefWords, Frame, Lap, Narrate, NarrationAsked,
                                PaceChanged, Slowing)
from iagent.live.narrator import chunks
from iagent.live.pipeline import Component, enrich, on

CAUSE_MEANINGS = {c.name: c.doc for c in phrasing.CAUSES}


class Debrief(Component):
    def start(self):
        self.results: list[list] = []  # per counted lap: the corners at pace
        self._reset(None)

    def _reset(self, stretch):
        self._stretch, self._words, self._waiting, self._due_at, self._done = stretch, None, False, None, False

    @on(Lap)
    def lap(self, e: Lap):
        self.results.append(e.results)

    @on(PaceChanged)
    def pace(self, e: PaceChanged):
        self._reset(self.state.stretch if e.mode == "tranquille" else None)

    @on(Slowing)
    def ask(self, e: Slowing):
        if not self.settings.debrief:
            return
        facts = self.facts()
        if facts["topics"] or facts["lap_times"]:
            self.emit(Narrate(kind="debrief", reply=DebriefWords.name, stretch=e.stretch, payload={"facts": facts}))

    @on(NarrationAsked)
    def asked(self, e: NarrationAsked):
        if e.reply == DebriefWords.name and e.stretch == self._stretch:
            self._waiting = True

    @enrich(DebriefWords)
    def words(self, e: DebriefWords):
        fits = e.stretch == self._stretch and self.state.mode == "tranquille" and not self._done
        e.status = "none" if not e.text else "used" if fits else "too late"
        if e.text and fits:
            self._words = e.text
        elif e.stretch == self._stretch:
            self._waiting = False  # nothing came: don't wait for it

    @on(CoolDown)
    def due(self, e: CoolDown):
        if self.settings.debrief and e.stretch == self._stretch:
            self._due_at = e.at

    @on(Frame)
    def give(self, e: Frame):
        if self._due_at is None or self._done:
            return
        waiting = self._waiting and self._words is None and e.at < self._due_at + self.settings.narrate_wait_s
        if waiting:
            return
        self._done = True
        lines = chunks(self._words) if self._words else self.lines()
        for i, text in enumerate(lines):
            self.emit(DebriefLine(text=text, index=i))
        if lines:
            self.emit(DebriefGiven(by="coach" if self._words else "template", text=" ".join(lines)))
        self.state.debriefs += 1

    def topics(self) -> list[dict]:
        """The focus, then the biggest losses over the last laps at pace."""
        s, st = self.settings, self.state
        by_corner: dict[int, list] = {}
        for lap in [lap for lap in self.results if lap][-3:]:
            for r in lap:
                if r.delta_s <= s.incident_s:
                    by_corner.setdefault(r.corner, []).append(r)
        mean = {c: sum(r.delta_s for r in rs) / len(rs) for c, rs in by_corner.items()}
        focus = st.focus_corners
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
        return phrasing.debrief_lines(self.topics(), self.state.lap_times, self.state.debriefs)

    def facts(self) -> dict:
        """What the debrief is about, for the narrator."""
        times = self.state.lap_times[-3:]
        topics = [{k: v for k, v in t.items() if k not in ("ref", "mine")} for t in self.topics()]
        return {"track": self.ctx.session.track_name, "laps_done": self.state.lap, "focus": self.state.focus_label,
                "topics": topics, "lap_times": [round(t, 2) for t in times],
                "lap_spread_s": round(max(times) - min(times), 2) if len(times) >= 2 else None,
                "cause_meanings": CAUSE_MEANINGS, "crewchief": self.settings.crewchief, "fallback": self.lines()}
