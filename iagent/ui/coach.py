"""The coach chat behind the web UI: one turn of the coach (`iagent.agent`) per message, resumed
by session id, its events sent to the browser as JSON lines.

Events (one JSON object per line):
- {"type": "run", "run_id"}             first, so the browser can stop the run
- {"type": "session", "session_id"}     the conversation to resume next time
- {"type": "text_start"}                a new block of the coach's text begins
- {"type": "text", "text"}              more of that text
- {"type": "tool", "id", "command"}     the coach ran a command (shown, not trusted)
- {"type": "ui", "action"}              `iagent ui show` succeeded: highlight this on screen
- {"type": "done", "session_id", "is_error"}
- {"type": "error", "message"}
"""

import json
from pathlib import Path
from typing import Iterator

from iagent.agent import ClaudeCode, StreamParser  # noqa: F401  (StreamParser: re-exported for callers)

SYSTEM_PROMPT = """\
You are the driver's coach inside the iRacing Coach lap review screen. The driver sees a track map,
a corner-by-corner table and telemetry traces of their lap against a ghost lap. Each message starts
with a <screen> block saying what is on screen: the lap, the ghost, the selected corners and any
zoomed distance range. Use the `iagent` CLI (see the telemetry skill) for every number; never guess.

Point at what you talk about: run `iagent ui show` with `--corner N` (repeatable), `--from M --to M`
(metres) to zoom the traces, `--view corner` to open the corner view (both racing lines, the line
offset and the corner's traces) for the first --corner, `--view lap` to go back to the whole lap, or
`--lap ID --ref ID` to switch laps. Do this whenever you discuss a specific corner or stretch.

Keep replies short: a few sentences, numbers with units, and one thing to work on."""


def screen_context(ctx: dict) -> str:
    """The <screen> block sent with each message, from the browser's view of the review."""
    lines = []
    if ctx.get("page") == "corner":
        lines.append("view: corner view (racing lines, line offset and traces for one corner)")
    if ctx.get("page") == "session":
        lines.append("view: review of a coached session (after it, not while driving). This page has no map or "
                     "traces to point at: don't run `iagent ui show`. Answer in a few short paragraphs.")
    if ctx.get("page") == "live":
        lines.append("view: live session. The driver is on track and hears your reply spoken: answer in at most "
                     "two short sentences, plain words, no markdown, no lists.")
    if ctx.get("track"):
        lines.append(f"track: {ctx['track']}  car: {ctx.get('car', '')}")
    if ctx.get("lap"):
        lines.append(f"lap: {ctx['lap']}")
    if ctx.get("ref"):
        lines.append(f"ghost: {ctx['ref']}" + (f" ({ctx['ref_driver']})" if ctx.get("ref_driver") else ""))
    if ctx.get("corners"):
        lines.append("selected corners: " + ", ".join(f"T{c}" for c in ctx["corners"]))
    if ctx.get("range"):
        a, b = ctx["range"]
        lines.append(f"zoomed to: {a:.0f}-{b:.0f} m")
    for line in ctx.get("live") or []:
        lines.append(line)
    return "<screen>\n" + "\n".join(lines) + "\n</screen>"


class CoachRuns(ClaudeCode):
    """The lap review's coach: its system prompt and the screen the driver is looking at."""

    def __init__(self, workspace: Path, claude: str | None = None):
        super().__init__(workspace, claude)

    def run(self, message: str, context: dict, session_id: str | None) -> Iterator[str]:
        """Stream one coach turn as JSON lines (a sync generator: Starlette runs it in a thread)."""
        prompt = f"{screen_context(context)}\n\n{message}"
        for event in self.turn(prompt, SYSTEM_PROMPT, session_id):
            yield _line(event)


def _line(event: dict) -> str:
    return json.dumps(event) + "\n"
