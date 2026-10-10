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
from iagent.live.events import define_event
from iagent.live.pipeline import Component

SILENT = "SILENT"
WAKE_MIN_GAP_S = 30.0  # wake-ups at most this often (each is a model turn)


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


define_event("narrate", "Ask the engineer (the model) for its words.", {
    "kind": "what it's for (a key of PROMPTS)", "reply": "the event the words come back as",
    "stretch": "passed back with the reply", "rule": "passed back with the reply",
    "min_gap_s": "skip it if one of this kind was asked for less than this long ago",
}, internal=("facts", "wake", "state", "text"))
define_event("narration_asked", "The engineer is working on its words.", {
    "kind": "what for", "reply": "the event they'll come back as", "stretch": "as asked"})
define_event("wake", "A rule woke the engineer.", {
    "rule": "the rule", "message": "its message", "values": "what it saw", "description": "the rule's description"},
             log=True)
define_event("coach_words", "The engineer's words to say (a radio check, a reply to a wake-up or a question).",
             {"text": "the words, or none", "rule": "the rule that woke it, if one did", "kind": "what they answer"},
             log=True)
define_event("session_end", "The session is over (the frames stopped).")
define_event("notes_written", "The engineer wrote up its notes after the session.", {"text": "what it said it changed"},
             log=True)

# How each kind of request is put to the engineer: kind -> (the narrate event's fields -> prompt).
PROMPTS: dict[str, Callable[[dict], str]] = {
    "briefing": lambda f: eng.briefing_prompt(f["facts"]),
    "debrief": lambda f: eng.debrief_prompt(f["facts"]),
    "wake": lambda f: eng.wake_prompt(f["wake"], f["state"]),
    "question": lambda f: eng.question_prompt(f["text"]),
    "wrap_up": lambda f: eng.wrap_up_prompt(f["facts"]),
}


class Radio(Component):
    """Between the pipeline and the engineer (if there is one: `ctx.engineer`)."""

    def on_narrate(self, e):
        engineer = self.ctx.engineer
        if engineer is None:
            return
        echo = {k: e.get(k) for k in ("stretch", "rule") if e.get(k) is not None}
        asked_at, reply_event = e.at, e["reply"]

        def reply(text: str | None) -> None:
            self.pipe.post(reply_event, text=clean(text), asked_at=asked_at, **echo,
                           **({"kind": e["kind"]} if reply_event == "coach_words" else {}))
        context = self.ctx.radio_context() if self.ctx.radio_context else {}
        if engineer.request(e["kind"], PROMPTS[e["kind"]](e.fields), context, reply, e.get("min_gap_s") or 0.0):
            self.emit("narration_asked", kind=e["kind"], reply=reply_event, stretch=e.get("stretch"))

    # --- what the engineer hears and is asked, as a real one would be --------------------------

    def on_line(self, e):
        if self.ctx.engineer is not None and e["status"] == "said" and e["kind"] != "coach":
            minutes, seconds = divmod(e.at, 60)
            self.ctx.engineer.heard(f"[{int(minutes):02d}:{seconds:04.1f}] coach ({e['kind']}): {e['text']}")

    def on_lap(self, e):
        if self.ctx.engineer is None:
            return
        gap = f", {e['gap_s']:+.2f} s vs the reference" if e.get("gap_s") is not None else ""
        worst = f", most lost at {e['worst_name']} ({e['worst_delta_s']:+.2f} s)" if e.get("worst_name") else ""
        self.ctx.engineer.heard(f"lap {e['lap']}: {e['lap_time']:.3f} s{gap}, {e['pace']}{worst}")

    def on_session_start(self, e):
        if self.settings.briefing:
            self.emit("narrate", kind="briefing", reply="coach_words", facts=self._plan())

    def on_session_end(self, e):
        if self.state["lap"] > 0:
            self.emit("narrate", kind="wrap_up", reply="notes_written", facts=self._summary())

    def _plan(self) -> dict:
        ctx, st = self.ctx, self.state
        return {"track": ctx.session.track_name, "track_key": ctx.session.track_key, "car": ctx.session.car_name,
                "notes": f"notes/{ctx.session.track_key}.md", "reference_lap_s": ctx.plan.ref_lap_time,
                "focus": st["focus_label"], "learning_laps": self.settings.learning_laps,
                "rules": [{"id": r.id, "description": r.description} for r in (ctx.rules or [])],
                "crewchief": self.settings.crewchief}

    def _summary(self) -> dict:
        st = self.state
        return {**self._plan(), "laps": st["lap"], "lap_times": [round(t, 2) for t in st["lap_times"]],
                "best_lap": st["best_lap"], "struggling": sorted(st["struggling"]),
                "focus_log": [{k: f[k] for k in ("label", "set_lap", "done_lap")} for f in st["focus_log"]]}
