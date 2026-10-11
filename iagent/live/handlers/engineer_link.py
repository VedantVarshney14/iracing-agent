"""The pipeline's link to the session's engineer (`iagent.live.engineer`).

It tells the engineer what went out on the radio and the laps driven (as a real engineer hears
them), takes `AskEngineer` requests to it, and posts its answers back through the pipeline's inbox
as `EngineerReply`, so the coach never waits. If no answer comes in time, the coach says its own
words. Corner cues are never generated: they must be instant and exactly timed.
"""

from iagent.live.events import AskEngineer, EngineerAsked, EngineerReply, Lap, Line, SessionEnd, SessionStart
from iagent.live.pipeline import EventHandler, on


class EngineerLink(EventHandler):
    """Between the pipeline and the engineer, if there is one (`ctx.engineer`)."""

    @on(AskEngineer)
    def ask(self, e: AskEngineer):
        engineer = self.ctx.engineer
        if engineer is None:
            return

        def reply(text: str | None) -> None:
            self.pipe.post(EngineerReply(kind=e.kind, text=(text or "").strip() or None, asked_at=e.at, rule=e.rule,
                                         stretch=e.stretch))
        context = self.ctx.radio_context() if self.ctx.radio_context else {}
        if engineer.request(e.kind, e.payload, context, reply, e.min_gap_s):
            self.emit(EngineerAsked(kind=e.kind, stretch=e.stretch))

    # --- what the engineer hears, and is asked at the start and end ---------------------------

    @on(Line)
    def heard(self, e: Line):
        if self.ctx.engineer is not None and e.status == "said" and e.kind != "engineer":  # not its own words
            minutes, seconds = divmod(e.at, 60)
            self.ctx.engineer.heard(f"[{int(minutes):02d}:{seconds:04.1f}] coach ({e.kind}): {e.text}")

    @on(Lap)
    def lap(self, e: Lap):
        if self.ctx.engineer is None:
            return
        gap = f", {e.gap_s:+.2f} s vs the reference" if e.gap_s is not None else ""
        worst = f", most lost at {e.worst_name} ({e.worst_delta_s:+.2f} s)" if e.worst_name else ""
        self.ctx.engineer.heard(f"lap {e.lap}: {e.lap_time:.3f} s{gap}, {e.pace}{worst}")

    @on(SessionStart)
    def briefing(self, e: SessionStart):
        if self.settings.briefing:
            self.emit(AskEngineer(kind="briefing", payload={"facts": self._plan()}))

    @on(SessionEnd)
    def wrap_up(self, e: SessionEnd):
        if self.state.lap > 0:
            self.emit(AskEngineer(kind="wrap_up", payload={"facts": self._summary()}))

    def _plan(self) -> dict:
        ctx, st = self.ctx, self.state
        return {"track": ctx.session.track_name, "track_key": ctx.session.track_key, "car": ctx.session.car_name,
                "notes": f"notes/{ctx.session.track_key}.md", "reference_lap_s": ctx.plan.ref_lap_time,
                "focus": st.focus_label, "learning_laps": self.settings.learning_laps,
                "rules": [{"id": r.id, "description": r.description} for r in (ctx.rules or [])],
                "crewchief": self.settings.crewchief}

    def _summary(self) -> dict:
        st = self.state
        return {**self._plan(), "laps": st.lap, "lap_times": [round(t, 2) for t in st.lap_times],
                "best_lap": st.best_lap, "struggling": sorted(st.struggling),
                "focus_log": [{k: f[k] for k in ("label", "set_lap", "done_lap")} for f in st.focus_log]}
