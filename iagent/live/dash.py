"""Hands what the coach says to the wheel dashboard: a SimHub plugin reads this file."""

import json
import os
from pathlib import Path

DEFAULT_PATH = Path.home() / ".iagent" / "dash.json"

# The dashboard has a slot per kind of line; "answer" and "rule" lines show as the cue.
SLOTS = {
    "approach": "cue",
    "answer": "cue",
    "rule": "cue",
    "feedback": "feedback",
    "summary": "summary",
    "focus": "focus",
}


class DashVoice:
    """Wraps the real voice and keeps the latest line of each kind in a JSON file, written whole
    each time so the SimHub plugin never reads half of it. `seq` goes up with every line."""

    def __init__(self, voice, path: Path = DEFAULT_PATH):
        self._voice = voice
        self._path = path
        self._lines = {"cue": "", "feedback": "", "summary": "", "focus": ""}
        self._seq = 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write()

    def duration(self, text):
        return self._voice.duration(text)

    def play(self, utterance, now):
        self._voice.play(utterance, now)
        self._lines[SLOTS[utterance.kind]] = utterance.text
        self._seq += 1
        self._write()

    def stop(self):
        self._voice.stop()

    def __getattr__(self, name):  # prepare / close, when the real voice has them
        return getattr(self._voice, name)

    def _write(self):
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self._lines, "seq": self._seq}))
        os.replace(tmp, self._path)
