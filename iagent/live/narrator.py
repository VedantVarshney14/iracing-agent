"""The coach's own words on the radio: where the model is in the loop anyway, it says the line.

The live coach decides *what* matters and *when* there's time to say it, from telemetry alone.
Where a model is asked anyway (a rule that wakes the coach) or there's time for it to answer
(a cool-down lap: the request goes out as the driver slows, the reply is wanted ~15 s later),
the coach (`claude -p`, the driver's own Claude Code) puts it in its own words: like an
engineer, not a template. The facts come from the live coach and are given, never re-derived.

Nothing waits on it: requests run on their own thread, a reply is handed back through the live
session's call queue, and if none comes in time (no `claude`, a slow model, a timeout) the
coach says its own phrasing instead. Corner cues are never generated: they must be instant
and exactly timed.
"""

import json
import logging
import re
import threading
import time
from typing import Callable

logger = logging.getLogger("iagent.live")

SILENT = "SILENT"
WAKE_MIN_GAP_S = 30.0  # wake-ups at most this often (each is a model run)

Ask = Callable[[str, dict], str | None]  # (prompt, screen context) -> the reply, blocking


class Narrator:
    """Runs model requests one per kind at a time, off the coach's thread."""

    def __init__(self, ask: Ask):
        self._ask = ask
        self._busy: set[str] = set()
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def request(self, kind: str, prompt: str, context: dict, on_reply: Callable[[str | None], None],
                min_gap_s: float = 0.0) -> bool:
        """Ask in the background; ON_REPLY gets the reply (None: nothing, or it failed). False if
        a request of this kind is already running, or the last one was under MIN_GAP_S ago."""
        now = time.monotonic()
        with self._lock:
            if kind in self._busy or now - self._last.get(kind, -1e9) < min_gap_s:
                return False
            self._busy.add(kind)
            self._last[kind] = now

        def run():
            reply = None
            try:
                reply = self._ask(prompt, context)
            except Exception:  # never take the live session down
                logger.exception("Narration (%s) failed", kind)
            finally:
                with self._lock:
                    self._busy.discard(kind)
            on_reply(clean(reply))
        threading.Thread(target=run, name=f"narrate-{kind}", daemon=True).start()
        return True


def ask_with(runs) -> Ask:
    """A blocking ask over the UI's CoachRuns (`claude -p` with the coach plugin)."""
    def ask(prompt: str, context: dict) -> str | None:
        return final_text(runs.run(prompt, context, None))
    return ask


def final_text(lines) -> str | None:
    """The coach's last block of text from a CoachRuns stream (the answer, not the narration
    before its tool calls)."""
    parts: list[str] = []
    for line in lines:
        event = json.loads(line)
        if event["type"] == "text_start":
            parts = []
        elif event["type"] == "text":
            parts.append(event["text"])
    return "".join(parts).strip() or None


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


_RADIO = """\
You're the driver's coach on the radio during a live iRacing session. Whatever you reply is
spoken to them by text-to-speech, word for word. Talk like a good race engineer and driving coach:
natural, calm, specific, encouraging but honest. Plain spoken English: no markdown, no lists, no
quotes, numbers the way people say them ("about twenty metres", "three tenths"). Use the facts
below as given (the live coach measured them); don't run commands unless you really must,
because the driver is waiting."""


def debrief_prompt(facts: dict) -> str:
    lines = [_RADIO, "",
             "The driver is on a cool-down lap and can listen for about twenty seconds. Give them the "
             "debrief: what to work on next run and how, from the facts. Two to four short sentences, "
             "under 60 words. Mention at most two corners."]
    if facts.get("crewchief"):
        lines.append("CrewChief is running and reads lap times and gaps: don't repeat those.")
    lines += ["", "<facts>", json.dumps(facts, indent=1), "</facts>", "",
              "The live coach's own phrasing, if you have nothing better:"]
    lines += [f"- {t}" for t in facts.get("fallback", [])]
    lines.append("\nReply with only what to say.")
    return "\n".join(lines)


def wake_prompt(wake: dict, state: dict) -> str:
    pushing = state.get("mode") == "pushing"
    lines = [_RADIO, "",
             f"A rule you set has fired: {wake['rule']}" + (f" ({wake['description']})" if wake.get("description") else "")
             + ".", f"Its message: {wake['message']}", f"Values it saw: {json.dumps(wake.get('values') or {})}",
             f"The driver is {'pushing: at most one short sentence, under 12 words' if pushing else 'not pushing (out lap, cool-down or after a moment): up to two short sentences'}.",
             f"Lap {state.get('lap')}, focus: {state.get('focus_label') or 'none'}."]
    if state.get("crewchief"):
        lines.append("CrewChief is running and reads lap times, gaps and personal bests: don't repeat those.")
    lines += ["", "If something is worth saying to the driver now, reply with only that. If not, reply "
              f"{SILENT}. You may also change rules for later with `iagent rules` (they reload within ten "
              "seconds), but only if the driver asked for it or a rule is clearly wrong."]
    return "\n".join(lines)


ANSWER_EXPIRES_S = 90.0  # a reply waits this long for a straight

Post = Callable[[Callable], None]  # run fn(coach, now) on the coach's thread, between frames


class Radio:
    """Connects a running coach to the narrator: debriefs asked for and rule wake-ups answered,
    with every reply handed back to the coach's thread through POST."""

    def __init__(self, narrator: Narrator, post: Post, log: Callable[[dict], None],
                 context: Callable[[], dict] = dict, now: Callable[[], float | None] = lambda: None):
        self.narrator, self._post, self._log, self._context, self._now = narrator, post, log, context, now

    def attach(self, coach) -> None:
        coach.on_narrate = lambda facts, stretch: self.debrief(coach, facts, stretch)

    def debrief(self, coach, facts: dict, stretch: int) -> None:
        asked_at = self._now()

        def reply(text: str | None) -> None:
            def call(c, now: float) -> None:
                used = c.narrated(text, stretch)
                self._log({"type": "narration", "kind": "debrief", "at": now, "asked_at": asked_at, "text": text,
                           "status": "used" if used else ("none" if not text else "too late")})
            self._post(call)
        if not self.narrator.request("debrief", debrief_prompt(facts), self._context(), reply):
            coach.narrated(None, stretch)  # one already running: the coach's own phrasing then

    def wake(self, coach, wake: dict) -> None:
        """A rule woke the coach: it may answer on the radio."""
        from iagent.live.speech import ANSWER, Utterance

        focus = next((f["label"] for f in reversed(coach.focus_log) if f["cue"] == coach.focus), None)
        state = {"mode": coach.mode, "lap": coach._laps_done, "focus_label": focus, "crewchief": coach.settings.crewchief}

        def reply(text: str | None) -> None:
            def call(c, now: float) -> None:
                self._log({"type": "narration", "kind": "wake", "rule": wake["rule"], "at": now, "text": text,
                           "status": "said" if text else "none"})
                if text:
                    c.arbiter.say(Utterance(text, ANSWER, "coach", now, now + ANSWER_EXPIRES_S, rule=wake["rule"]))
            self._post(call)
        if not self.narrator.request("wake", wake_prompt(wake, state), self._context(), reply, WAKE_MIN_GAP_S):
            self._log({"type": "narration", "kind": "wake", "rule": wake["rule"], "at": wake.get("at"),
                       "status": "skipped", "text": None})
