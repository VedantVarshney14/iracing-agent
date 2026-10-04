"""The coach chat behind the web UI: one `claude -p` run per message, resumed by session id, its
stream-json output turned into a few simple events for the browser.

No API keys or per-token billing: this drives the driver's own Claude Code (a Claude
subscription, or a local model via ANTHROPIC_BASE_URL), exactly like the headless coach.

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
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Iterator

import iagent

RUN_TIMEOUT_S = 300
COACH_PLUGIN = Path(iagent.__file__).resolve().parents[1] / "coach"
ALLOWED_TOOLS = ["Bash(iagent *)", "Read", "Glob", "Grep", "Write", "Edit", "Skill"]

SYSTEM_PROMPT = """\
You are the driver's coach inside the iRacing Coach lap review screen. The driver sees a track map,
a corner-by-corner table and telemetry traces of their lap against a ghost lap. Each message starts
with a <screen> block saying what is on screen: the lap, the ghost, the selected corners and any
zoomed distance range. Use the `iagent` CLI (see the telemetry skill) for every number; never guess.

Point at what you talk about: run `iagent ui show` with `--corner N` (repeatable), `--from M --to M`
(metres) to zoom the traces, `--view corner` to show both racing lines, or `--lap ID --ref ID` to
switch laps. Do this whenever you discuss a specific corner or stretch of track.

Keep replies short: a few sentences, numbers with units, and one thing to work on."""


def screen_context(ctx: dict) -> str:
    """The <screen> block sent with each message, from the browser's view of the review."""
    lines = []
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
    return "<screen>\n" + "\n".join(lines) + "\n</screen>"


class CoachRuns:
    """Running coach processes, so the browser can stop one."""

    def __init__(self, workspace: Path, claude: str | None = None):
        self.workspace = workspace.resolve()
        self.claude = claude or os.environ.get("IAGENT_CLAUDE") or "claude"
        self._procs: dict[str, subprocess.Popen] = {}
        self._stopped: set[str] = set()
        self._lock = threading.Lock()

    def stop(self, run_id: str) -> bool:
        with self._lock:
            proc = self._procs.get(run_id)
        if proc is None:
            return False
        self._stopped.add(run_id)
        proc.kill()
        return True

    def command(self, session_id: str | None) -> list[str]:
        cmd = [self.claude, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--append-system-prompt", SYSTEM_PROMPT, "--permission-mode", "acceptEdits",
               "--allowedTools", *ALLOWED_TOOLS]
        if COACH_PLUGIN.is_dir():
            cmd += ["--plugin-dir", str(COACH_PLUGIN)]
        if model := os.environ.get("IAGENT_COACH_MODEL"):
            cmd += ["--model", model]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd

    def env(self) -> dict[str, str]:
        """The coach's shell finds this install's `iagent` and works on the UI's workspace."""
        env = dict(os.environ)
        env["IAGENT_WORKSPACE"] = str(self.workspace)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        return env

    def run(self, message: str, context: dict, session_id: str | None) -> Iterator[str]:
        """Stream one coach turn as JSON lines (a sync generator: Starlette runs it in a thread)."""
        run_id = uuid.uuid4().hex
        yield _line({"type": "run", "run_id": run_id})
        if shutil.which(self.claude) is None:
            yield _line({"type": "error", "message": f"Couldn't find `{self.claude}`. Install Claude Code, or set "
                         "IAGENT_CLAUDE to its path."})
            return
        prompt = f"{screen_context(context)}\n\n{message}"
        errors = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")  # never blocks the run
        try:
            proc = subprocess.Popen(
                self.command(session_id), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                text=True, encoding="utf-8", errors="replace", env=self.env(), cwd=self.workspace.parent,
            )
        except OSError as e:
            errors.close()
            yield _line({"type": "error", "message": f"Couldn't start the coach: {e}"})
            return
        with self._lock:
            self._procs[run_id] = proc
        timer = threading.Timer(RUN_TIMEOUT_S, proc.kill)
        timer.start()
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            parser = StreamParser()
            for raw in proc.stdout:
                for event in parser.feed(raw):
                    yield _line(event)
            proc.wait()
            if not parser.finished:
                errors.seek(0)
                err = errors.read().strip()
                stopped = run_id in self._stopped
                yield _line({"type": "error", "message": "Stopped." if stopped else (err[-500:] or "The coach exited early.")})
        finally:
            timer.cancel()
            with self._lock:
                self._procs.pop(run_id, None)
                self._stopped.discard(run_id)
            if proc.poll() is None:
                proc.kill()
            errors.close()


class StreamParser:
    """Claude Code's stream-json lines in, browser events out."""

    def __init__(self):
        self.finished = False
        self._ui_calls: set[str] = set()  # tool_use ids that ran `iagent ui show`

    def feed(self, raw: str) -> list[dict]:
        raw = raw.strip()
        if not raw:
            return []
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return []
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "init":
            return [{"type": "session", "session_id": msg.get("session_id")}]
        if kind == "stream_event":
            event = msg.get("event") or {}
            if event.get("type") == "content_block_start" and (event.get("content_block") or {}).get("type") == "text":
                return [{"type": "text_start"}]
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                return [{"type": "text", "text": delta.get("text", "")}]
            return []
        if kind == "assistant":
            out = []
            for block in (msg.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    args = block.get("input") or {}
                    command = args.get("command") or " ".join(
                        str(v) for v in (block.get("name"), args.get("skill") or args.get("file_path") or args.get("pattern")) if v
                    )
                    if "iagent ui show" in command:
                        self._ui_calls.add(block.get("id"))
                    out.append({"type": "tool", "id": block.get("id"), "command": command})
            return out
        if kind == "user":
            out = []
            for block in (msg.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result" and block.get("tool_use_id") in self._ui_calls and not block.get("is_error"):
                    action = _ui_action(block.get("content"))
                    if action is not None:
                        out.append({"type": "ui", "action": action})
            return out
        if kind == "result":
            self.finished = True
            return [{"type": "done", "session_id": msg.get("session_id"), "is_error": bool(msg.get("is_error"))}]
        return []


def _ui_action(content) -> dict | None:
    """The `ui_action` that `iagent ui show` printed, from a tool result's content."""
    if isinstance(content, list):
        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    text = str(content or "")
    for chunk in (text, *text.splitlines()):  # the whole output (pretty-printed), else line by line
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("ui_action"), dict):
            return data["ui_action"]
    return None


def _line(event: dict) -> str:
    return json.dumps(event) + "\n"
