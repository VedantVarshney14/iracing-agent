"""The debug log: the whole loop of a coached session, for working on the coach itself.

It's every pipeline event but frames and ticks (`sessions/live/debug/<id>.jsonl`): each rule
firing with the values it saw, each engineer turn with the exact prompt and reply, every line said
or dropped. Sessions run from the browser and from `iagent live run` both write one; the review
page shows it under "Debug: the whole loop".
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("iagent.live")

NOT_LOGGED = {
    "frame",
    "frame_done",
    "tick",
}  # too many, and nothing in them but the telemetry


def debug_log_path(root: Path, session_id: str) -> Path:
    """Where session SESSION_ID's debug log is in the workspace at ROOT."""
    return root / "sessions" / "live" / "debug" / f"{session_id}.jsonl"


class DebugLog:
    """Appends entries to a session's debug log, numbered in it. A log that can't be written never
    stops the coach."""

    def __init__(self, path: Path):
        self.path = path
        self._seq = 0

    def event(self, e) -> None:
        """A pipeline listener: write event E (unless it's a frame or a tick)."""
        if e.name not in NOT_LOGGED:
            self.write({"type": e.name, "at": e.at, **e.public()})

    def write(self, entry: dict) -> None:
        """Write ENTRY (a dict with a `type`), numbered and timestamped."""
        line = {"debug_seq": self._seq, **entry}
        self._seq += 1
        line.setdefault("wall", datetime.now(timezone.utc).isoformat())
        append(self.path, json.dumps(line, default=_plain))


def append(path: Path | None, line: str) -> None:
    """Append a line to a log (if there is one); a log that can't be written is only logged."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(line + "\n")
    except OSError:
        logger.exception("Couldn't write %s", path)


def _plain(value):
    """A value JSON can't write as it is: a numpy number as a number, anything else as text."""
    return value.item() if hasattr(value, "item") else str(value)
