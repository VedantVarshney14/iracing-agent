"""The session's race engineer: one Claude Code conversation for the whole session.

In real life one engineer works with the driver through a session. Before it they read their
notes and the plan and give a radio check; during it they hear everything said on the radio and
see the lap times, answer the driver's questions, pick things up when something they asked to be
told about happens (a rule's `wake`), and debrief on a cool-down lap; after it they debrief and
write it up. The next session's engineer starts from those notes: memory between sessions is the
notebook (the workspace notes), not an ever-longer conversation.

So the engineer here is:

- **one conversation per session**: a fixed session id, `--session-id` on the first turn and
  `--resume` after, so every turn knows what was said before;
- **one turn at a time**, most important first (the driver's question, the debrief, a rule's
  wake-up, the briefing). A request that's no use by the time its turn comes (a debrief once the
  driver is pushing again) is dropped, and the coach's own words are said instead;
- **listening**: each turn starts with what went out on the radio and the laps since the last one;
- **isolated**: its own conversation, the coach plugin and only the tools the coach needs, on the
  workspace; nothing from the driver's other Claude Code sessions.

The live coach never waits on it (`iagent.live.narrator.Radio` puts requests to it and hands the
replies back through the pipeline's inbox).
"""

import heapq
import itertools
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from iagent.agent import ClaudeCode, structured

logger = logging.getLogger("iagent.live")

SYSTEM = """\
You're the driver's race engineer and driving coach for this whole iRacing session, on the radio.
This conversation is the session: earlier turns are what you've already said and heard. Each turn
starts with what went out on the radio since your last turn (the live coach's cues and feedback,
which the driver heard) and the laps driven. Your reply is structured: what you put in `say` is
spoken to the driver by text-to-speech, word for word. Talk like a good race engineer:
natural, calm, specific, encouraging but honest, and consistent with what you've said before.
Plain spoken English: no markdown, no lists, no quotes, numbers the way people say them ("about
twenty metres", "three tenths"). Facts the live coach measured are given: use them as they are,
and only run `iagent` commands when you really need more, because the driver is waiting. When
there's nothing worth saying, `say` is null."""

# Replies are structured (`claude -p --json-schema`): no parsing of free text.
SAY = {"type": "object", "additionalProperties": False, "required": ["say"], "properties": {
    "say": {"type": ["string", "null"],
            "description": "the words spoken to the driver (plain spoken English), or null to say nothing"}}}
NOTES = {"type": "object", "additionalProperties": False, "required": ["changed"], "properties": {
    "changed": {"type": "string", "description": "one line: what you changed in the notes"}}}

Turn = Callable[[str, str, bool, dict], tuple[dict | None, bool]]  # (prompt, session id, resume, schema) -> (reply, exists)


@dataclass(order=True)
class _Request:
    priority: int
    seq: int
    kind: str = field(compare=False)
    prompt: str = field(compare=False)
    schema: dict = field(compare=False)
    context: dict = field(compare=False)
    on_reply: Callable[[str | None], None] = field(compare=False)
    deadline: float = field(compare=False)


class Engineer:
    def __init__(self, turn: Turn, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self._turn = turn
        self._exists = False  # the conversation has been started
        self._queue: list[_Request] = []
        self._busy: set[str] = set()  # kinds queued or running
        self._last: dict[str, float] = {}
        self._heard: list[str] = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._closed = False
        self.turns = 0

    @classmethod
    def over(cls, runner: ClaudeCode, session_id: str | None = None) -> "Engineer":
        """An engineer whose turns are the driver's Claude Code (`claude -p`)."""
        def turn(prompt: str, sid: str, resume: bool, schema: dict) -> tuple[dict | None, bool]:
            events = list(runner.turn(prompt, SYSTEM, sid, new_session=not resume, schema=schema))
            for e in events:
                if e["type"] == "error":
                    logger.warning("The engineer's turn failed: %s", e["message"])
            return structured(events), any(e["type"] in ("session", "done") for e in events)
        return cls(turn, session_id)

    # --- what it hears -------------------------------------------------------------------------

    def heard(self, line: str) -> None:
        """Something went out on the radio, or a lap was driven: part of the next turn."""
        with self._cv:
            self._heard.append(line)
            del self._heard[:-60]  # a turn needn't recap a whole stint

    # --- what it's asked -----------------------------------------------------------------------

    def request(self, kind: str, payload: dict, context: dict, on_reply: Callable[[str | None], None],
                min_gap_s: float = 0.0) -> bool:
        """Queue a turn of KIND (a key of TURNS) made from PAYLOAD; ON_REPLY gets the words (None:
        nothing to say, too late, or it failed). False if one of this kind is already waiting or
        running (questions always queue), or the last was under MIN_GAP_S ago."""
        spec = TURNS[kind]
        now = time.monotonic()
        with self._cv:
            if self._closed:
                return False
            if kind != "question" and (kind in self._busy or now - self._last.get(kind, -1e9) < min_gap_s):
                return False
            self._busy.add(kind)
            self._last[kind] = now
            heapq.heappush(self._queue, _Request(spec.priority, next(self._seq), kind, spec.prompt(payload), spec.schema,
                                                 context, on_reply, now + spec.wait_s))
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name="engineer", daemon=True)
                self._thread.start()
            self._cv.notify()
        return True

    def close(self, wait_s: float = 0.0) -> None:
        """No more requests; finish what's queued (waiting up to WAIT_S)."""
        with self._cv:
            self._closed = True
            self._cv.notify()
        if wait_s and self._thread is not None:
            self._thread.join(timeout=wait_s)

    def idle(self) -> bool:
        with self._cv:
            return not self._queue and not self._busy

    # --- the conversation ----------------------------------------------------------------------

    def _work(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._closed:
                    self._cv.wait()
                if not self._queue:
                    return
                req = heapq.heappop(self._queue)
                heard, self._heard = self._heard, []
            reply = None
            if time.monotonic() <= req.deadline:
                reply = self._take_turn(req, heard)
            else:
                logger.info("Engineer: dropped a %s request, too late to be useful", req.kind)
                with self._cv:
                    self._heard = heard + self._heard  # still news next turn
            with self._cv:
                self._busy.discard(req.kind)
            try:
                req.on_reply(reply)
            except Exception:
                logger.exception("Engineer: reply handler failed")

    def _take_turn(self, req: _Request, heard: list[str]) -> str | None:
        parts = []
        if heard:
            parts.append("<radio since your last turn>\n" + "\n".join(heard) + "\n</radio>")
        if req.context:
            parts.append("<session>\n" + _context(req.context) + "\n</session>")
        parts.append(req.prompt)
        try:
            reply, exists = self._turn("\n\n".join(parts), self.session_id, self._exists, req.schema)
        except Exception:
            logger.exception("Engineer: the turn failed")
            return None
        self._exists = self._exists or exists
        self.turns += 1
        return TURNS[req.kind].words(reply) if isinstance(reply, dict) else None


def _context(ctx: dict) -> str:
    lines = []
    for key in ("track", "car", "ref"):
        if ctx.get(key):
            lines.append(f"{key}: {ctx[key]}")
    lines += list(ctx.get("live") or [])
    return "\n".join(lines) or json.dumps(ctx)


# --- what each kind of turn asks ------------------------------------------------------------------

def briefing_prompt(f: dict) -> str:
    lines = ["The session is starting: the driver is heading out. Before you say anything, read your notes for "
             f"this track if there are any (`{f['notes']}` in the workspace) and the plan below.",
             "", "<plan>", json.dumps(f, indent=1), "</plan>", "",
             "Give a radio check for the out lap: one or two short sentences, hello and the plan for this run "
             "(what to focus on, from the plan or your notes). If the plan is empty and there are no notes, just "
             "a short radio check."]
    if f.get("crewchief"):
        lines.append("CrewChief is running: it does the spotter, lap times and gaps. Don't do those.")
    return "\n".join(lines)


def debrief_prompt(f: dict) -> str:
    lines = ["The driver is on a cool-down lap and can listen for about twenty seconds. Give them the debrief: what "
             "to work on next run and how, from the facts and what you've said before. Two to four short sentences, "
             "under 60 words. Mention at most two corners."]
    if f.get("crewchief"):
        lines.append("CrewChief is running and reads lap times and gaps: don't repeat those.")
    lines += ["", "<facts>", json.dumps(f, indent=1), "</facts>", "",
              "The live coach's own phrasing, if you have nothing better:"]
    lines += [f"- {t}" for t in f.get("fallback", [])]
    return "\n".join(lines)


def wake_prompt(wake: dict, state: dict) -> str:
    pushing = state.get("mode") == "pushing"
    lines = [f"A rule you set has fired: {wake['rule']}" + (f" ({wake['description']})" if wake.get("description") else "")
             + ".", f"Its message: {wake['message']}", f"Values it saw: {json.dumps(wake.get('values') or {})}",
             f"The driver is {'pushing: at most one short sentence, under 12 words' if pushing else 'not pushing (out lap, cool-down or after a moment): up to two short sentences'}.",
             f"Lap {state.get('lap')}, focus: {state.get('focus_label') or 'none'}."]
    if state.get("crewchief"):
        lines.append("CrewChief is running and reads lap times, gaps and personal bests: don't repeat those.")
    lines += ["", "If something is worth saying to the driver now, put it in `say`; if not, `say` is null. You may also "
              "change rules for later with `iagent rules` (they reload within ten seconds), but only if the driver asked "
              "for it or a rule is clearly wrong."]
    return "\n".join(lines)


def question_prompt(text: str) -> str:
    return (f"The driver asks, on the radio: {text}\n\nThey're on track and hear your reply spoken: answer in at most "
            "two short sentences.")


def wrap_up_prompt(f: dict) -> str:
    return ("The session is over; the driver is back in the pits. Nothing is spoken now. Update your notes for this "
            f"track (`{f['notes']}` in the workspace; create it if there isn't one): what the driver worked on, what "
            "changed, what still costs the most, and what to start with next session. Keep it short and keep what's "
            "still true from before. In `changed`, one line saying what you changed.\n\n<facts>\n"
            + json.dumps(f, indent=1) + "\n</facts>")


@dataclass(frozen=True)
class TurnKind:
    """A kind of turn: how urgent, how long it stays worth answering, what's asked, and the
    structured reply wanted (and which of its fields are the words)."""

    priority: int  # lower first
    wait_s: float  # dropped if it can't start within this
    prompt: Callable[[dict], str]  # the request's payload -> the prompt
    schema: dict = field(default_factory=lambda: SAY)
    words: Callable[[dict], str | None] = lambda r: r.get("say")


TURNS: dict[str, TurnKind] = {
    "question": TurnKind(0, 60.0, lambda p: question_prompt(p["text"])),
    "debrief": TurnKind(1, 20.0, lambda p: debrief_prompt(p["facts"])),
    "wake": TurnKind(2, 20.0, lambda p: wake_prompt(p["wake"], p["state"])),
    "briefing": TurnKind(3, 90.0, lambda p: briefing_prompt(p["facts"])),
    "wrap_up": TurnKind(4, 600.0, lambda p: wrap_up_prompt(p["facts"]), NOTES, lambda r: r.get("changed")),
}
