"""One focus at a time.

After the learning laps one cue becomes the focus: the corners losing the most time over the last
two laps at pace. Once it's within `loss_s` on two laps at pace it's done and the next is picked.
A focus carried over from the last session (the next-session plan) starts there and is re-checked
after two pushing laps. The driver or the coach can set it (`set_focus`).

Sets `focus_news` on `Lap` ("Focus now: Turn 5."); saying it is a rule (`builtin_rules`).
"""

from iagent.live import phrasing
from iagent.live.events import Error, FocusChanged, Lap, SessionStart, SetFocus
from iagent.live.pipeline import Component, enrich, on


class Focus(Component):
    def start(self):
        self.manual = False
        self.cue_losses: list[dict[int, float]] = []  # per lap at pace: cue -> time lost (s)
        self._carried = False
        carried = self.ctx.carried_focus
        cue = self.ctx.plan.cue_for(carried) if carried is not None else None
        if cue is not None:
            self._carried = True
            self._set(cue, manual=False, extra={"carried": True})

    @on(SessionStart)
    def carried_over(self, e: SessionStart):
        if self._carried:
            self.emit(FocusChanged(focus=self._entry(), by="coach"))

    def label(self, cue) -> str:
        return phrasing.cue_label([self.ctx.cmap.get(c).name for c in cue.corners], cue.corners)

    def _entry(self) -> dict | None:
        focus = self.state.focus
        return next((f for f in reversed(self.state.focus_log) if f["cue"] == focus), None) if focus is not None else None

    def _set(self, cue, manual: bool, extra: dict | None = None) -> dict | None:
        st = self.state
        if cue is None:
            st.focus, st.focus_corners, st.focus_label = None, [], None
            self.manual = False
            return None
        entry = {"cue": cue.corner, "corners": cue.corners, "label": self.label(cue), "set_lap": st.lap,
                 "done_lap": None, "manual": manual, **(extra or {})}
        st.focus_log = [*st.focus_log, entry]
        st.focus, st.focus_corners, st.focus_label, st.focus_heard = cue.corner, list(cue.corners), entry["label"], False
        self.manual = manual
        return entry

    @on(SetFocus)
    def set_by_driver(self, e: SetFocus):
        cue = self.ctx.plan.cue_for(e.corner) if e.corner is not None else None
        if e.corner is not None and cue is None:
            self.emit(Error(message=f"No cue covers T{e.corner}."))
            return
        self._set(cue, manual=True)
        self.emit(FocusChanged(focus=self._entry(), by="driver"))

    @enrich(Lap)
    def after_lap(self, e: Lap):
        before = self._entry()
        enough = len(e.results) >= max(1, len(self.ctx.cmap.corners) // 2)  # most corners at pace
        e.focus_news = self.update(e.results) if enough else None
        e.focus = self.state.focus
        after = self._entry()
        if after is not None and after is not before:
            self.emit(FocusChanged(focus=after, by="coach"))

    def update(self, results) -> str | None:
        """After a lap at pace: is the focus done, and what's next? Returns what to announce."""
        s, st, plan = self.settings, self.state, self.ctx.plan
        losses: dict[int, float] = {}
        judged: dict[int, int] = {}
        for r in results:
            cue = plan.cue_for(r.corner)
            if cue is not None and r.delta_s <= s.incident_s:
                losses[cue.corner] = losses.get(cue.corner, 0.0) + r.delta_s
                judged[cue.corner] = judged.get(cue.corner, 0) + 1
        # A cue counts only when all its corners were judged (pushing through all of them).
        losses = {k: v for k, v in losses.items() if judged[k] == len(plan.cue_for(k).corners)}
        self.cue_losses.append(losses)
        if not s.focus or st.lap < s.learning_laps:
            return None
        news = []
        if self._carried and not self.manual and st.focus is not None:
            replaced = self._recheck_carried()
            if replaced:
                news.append(replaced)
        if st.focus is not None:
            recent = [lap[st.focus] for lap in self.cue_losses if st.focus in lap][-2:]
            entry = self._entry()
            if len(recent) == 2 and all(x < s.loss_s for x in recent) and st.lap > entry["set_lap"]:
                entry["done_lap"] = st.lap
                news.append(f"{entry['label']} sorted.")
                self._set(None, False)
        if st.focus is None:
            pick = self.pick()
            if pick is not None:
                cue = plan.cue_for(pick)
                entry = self._set(cue, manual=False)
                hint = next((st.hints[c] for c in cue.corners if c in st.hints), None)
                news.append(f"Focus now: {entry['label']}." + (f" {hint}" if hint else ""))
        return " ".join(news) or None

    def _recheck_carried(self) -> str | None:
        """After two pushing laps, a focus carried over from last time stays only if it's still
        worth it: not yet sorted, and nothing else losing clearly more."""
        s, st = self.settings, self.state
        values = [lap[st.focus] for lap in self.cue_losses if st.focus in lap]
        if len(values) < 2:
            return None
        self._carried = False
        entry = self._entry()
        mine = sum(values[-2:]) / 2
        if mine < s.focus_min_s:
            entry["done_lap"] = st.lap
            self._set(None, False)
            return f"{entry['label']} is fine now."
        best = self.pick()
        if best is not None and best != st.focus:
            other = [lap[best] for lap in self.cue_losses[-2:] if best in lap]
            if other and sum(other) / len(other) > mine + 0.1:
                entry["done_lap"] = st.lap
                entry["replaced"] = True
                self._set(None, False)
        return None

    def pick(self) -> int | None:
        s = self.settings
        recent = self.cue_losses[-2:]
        done = {f["cue"] for f in self.state.focus_log if f["done_lap"] is not None}
        mean = {}
        for cue in self.ctx.plan.cues:
            values = [lap[cue.corner] for lap in recent if cue.corner in lap]  # pushing laps only
            if values:
                mean[cue.corner] = sum(values) / len(values)
        candidates = {k: v for k, v in mean.items() if v >= s.focus_min_s and k not in done}
        return max(candidates, key=candidates.get) if candidates else None
