"""The radio link to the session's engineer: the coach's own words, where there's time for them.

The live coach decides *what* matters and *when* there's time to say it, from telemetry alone.
Where the model is in the loop anyway, it does the talking: the engineer (`iagent.live.engineer`,
one Claude Code conversation for the session) gives the radio check as the driver heads out,
answers when a rule wakes it, words the cool-down debrief, and writes up its notes at the end.

`Radio` is the pipeline's side of that. It tells the engineer what went out on the radio and the
laps driven (as a real engineer hears it), puts `narrate` requests to it, and hands the replies
back through the pipeline's inbox as the event the request named, so the coach never waits; if
no reply comes in time, the coach says its own words. Corner cues are never generated: they must
be instant and exactly timed.
"""

import re
from typing import Callable

from iagent.live import engineer as eng
from iagent.live.events import EVENTS, CoachWords, Lap, Line, Narrate, NarrationAsked, NotesWritten, SessionEnd, SessionStart
from iagent.live.pipeline import Component, on

SILENT = "SILENT"


def clean(reply: str | None) -> str | None:
    """Spoken text only: no markdown, quotes or stage directions; SILENT means nothing to say."""
    if not reply:
        return None
    text = re.sub(r"[*_`#>]+", "", reply).strip().strip('"').strip()
    text = re.sub(r"\s+", " ", text)
    if not text or text.upper().rstrip(".") == SILENT:
        return None
    return text


def chunks(text: str, max_chars: int = 140) -> list[str]:
    """Split a longer reply into radio-sized pieces at sentence ends, so a car alongside or the
    next corner can come between them."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out: list[str] = []
    for s in sentences:
        if out and len(out[-1]) + 1 + len(s) <= max_chars:
            out[-1] = f"{out[-1]} {s}"
        else:
            out.append(s)
    return [c for c in out if c]


# How each kind of request is put to the engineer: kind -> (the request's payload -> prompt).
PROMPTS: dict[str, Callable[[dict], str]] = {
    "briefing": lambda p: eng.briefing_prompt(p["facts"]),
    "debrief": lambda p: eng.debrief_prompt(p["facts"]),
    "wake": lambda p: eng.wake_prompt(p["wake"], p["state"]),
    "question": lambda p: eng.question_prompt(p["text"]),
    "wrap_up": lambda p: eng.wrap_up_prompt(p["facts"]),
}


class Radio(Component):
    """Between the pipeline and the engineer (if there is one: `ctx.engineer`)."""

    @on(Narrate)
    def ask(self, e: Narrate):
        engineer = self.ctx.engineer
        if engineer is None:
            return
        reply_cls, asked_at = EVENTS[e.reply], e.at

        def reply(text: str | None) -> None:
            self.pipe.post(reply_cls(text=clean(text), asked_at=asked_at, stretch=e.stretch, rule=e.rule, kind=e.kind))
        context = self.ctx.radio_context() if self.ctx.radio_context else {}
        if engineer.request(e.kind, PROMPTS[e.kind](e.payload), context, reply, e.min_gap_s):
            self.emit(NarrationAsked(kind=e.kind, reply=e.reply, stretch=e.stretch))

    # --- what the engineer hears and is asked, as a real one would be --------------------------

    @on(Line)
    def heard(self, e: Line):
        if self.ctx.engineer is not None and e.status == "said" and e.kind != "coach":
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
            self.emit(Narrate(kind="briefing", reply=CoachWords.name, payload={"facts": self._plan()}))

    @on(SessionEnd)
    def wrap_up(self, e: SessionEnd):
        if self.state.lap > 0:
            self.emit(Narrate(kind="wrap_up", reply=NotesWritten.name, payload={"facts": self._summary()}))

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
