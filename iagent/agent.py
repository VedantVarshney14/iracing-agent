"""The coach agent's runner: the driver's own Claude Code, headless.

No API keys or per-token billing: `claude -p` with the coach plugin (a Claude subscription, or a
local model via ANTHROPIC_BASE_URL), allowed to run `iagent` and read and write the workspace.
Its stream-json output is turned into a few simple events:

- {"type": "run", "run_id"}             first, so a caller can stop the run
- {"type": "session", "session_id"}     the conversation, to resume next time
- {"type": "text_start"}                a new block of the coach's text begins
- {"type": "text", "text"}              more of that text
- {"type": "tool", "id", "command"}     the coach ran a command (shown, not trusted)
- {"type": "ui", "action"}              `iagent ui show` succeeded: highlight this on screen
- {"type": "done", "session_id", "is_error"}
- {"type": "error", "message"}

Used by the lap review's chat (`iagent.ui.coach`) and the live session's engineer
(`iagent.live.engineer`), each with its own system prompt and conversation.
"""

import json
import os
from abc import ABC, abstractmethod
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Iterable, Iterator

import iagent

RUN_TIMEOUT_S = 300
COACH_PLUGIN = Path(iagent.__file__).resolve().parents[1] / "coach"
ALLOWED_TOOLS = ["Bash(iagent *)", "Read", "Glob", "Grep", "Write", "Edit", "Skill"]


class Agent(ABC):
    """An agent harness the coach runs in: one turn of a conversation at a time. Claude Code is
    the one we use; another harness (Codex, OpenCode, Goose) is another subclass, the same few
    lines with a different command."""

    @abstractmethod
    def turn(self, prompt: str, system_prompt: str, session_id: str | None = None,
             new_session: bool = False) -> Iterator[dict]:
        """One turn, as events (see the module docstring), starting with {"type": "run", "run_id"}.
        SESSION_ID continues that conversation, or starts it with that id when NEW_SESSION."""

    @abstractmethod
    def stop(self, run_id: str) -> bool:
        """Stop a running turn."""


class ClaudeCode(Agent):
    """The driver's own Claude Code, run headless (`claude -p`) with the coach plugin, on the
    workspace. `--session-id` starts a conversation with a given id, `--resume` continues it."""

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

    def command(self, system_prompt: str, session_id: str | None = None, new_session: bool = False) -> list[str]:
        cmd = [self.claude, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--append-system-prompt", system_prompt, "--permission-mode", "acceptEdits",
               "--allowedTools", *ALLOWED_TOOLS]
        if COACH_PLUGIN.is_dir():
            cmd += ["--plugin-dir", str(COACH_PLUGIN)]
        if model := os.environ.get("IAGENT_COACH_MODEL"):
            cmd += ["--model", model]
        if session_id:
            cmd += ["--session-id" if new_session else "--resume", session_id]
        return cmd

    def env(self) -> dict[str, str]:
        """The coach's shell finds this install's `iagent` and works on the UI's workspace."""
        env = dict(os.environ)
        env["IAGENT_WORKSPACE"] = str(self.workspace)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        return env

    def turn(self, prompt: str, system_prompt: str, session_id: str | None = None,
             new_session: bool = False) -> Iterator[dict]:
        run_id = uuid.uuid4().hex
        yield {"type": "run", "run_id": run_id}
        if shutil.which(self.claude) is None:
            yield {"type": "error", "message": f"Couldn't find `{self.claude}`. Install Claude Code, or set "
                   "IAGENT_CLAUDE to its path."}
            return
        errors = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")  # never blocks the run
        try:
            proc = subprocess.Popen(
                self.command(system_prompt, session_id, new_session), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                text=True, encoding="utf-8", errors="replace", env=self.env(), cwd=self.workspace.parent,
            )
        except OSError as e:
            errors.close()
            yield {"type": "error", "message": f"Couldn't start the coach: {e}"}
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
                yield from parser.feed(raw)
            proc.wait()
            if not parser.finished:
                errors.seek(0)
                err = errors.read().strip()
                stopped = run_id in self._stopped
                yield {"type": "error", "message": "Stopped." if stopped else (err[-500:] or "The coach exited early.")}
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


def final_text(events: Iterable[dict]) -> str | None:
    """The coach's last block of text in a turn (the answer, not the narration before its tool calls)."""
    parts: list[str] = []
    for event in events:
        if event["type"] == "text_start":
            parts = []
        elif event["type"] == "text":
            parts.append(event["text"])
    return "".join(parts).strip() or None
