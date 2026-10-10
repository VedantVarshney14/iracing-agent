"""One live coaching session at a time, run inside `iagent ui` so the browser can start, watch
and stop it.

The coach runs on its own thread. Everything it does is logged as events (lines said, cut off or
dropped and why, laps, focus changes, the driver's questions and the answers), kept in memory for
the page and appended to `sessions/live/<id>.jsonl` in the workspace for review afterwards.
Anything the browser asks of the running coach (say an answer, change the focus) is queued and
done on the coach's thread between frames, so the coach itself needs no locking.
"""

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from iagent.live.coach import LiveCoach, Settings
from iagent.live.run import run
from iagent.live.speech import ANSWER, CapturedVoice, Utterance, Voice
from iagent.telemetry.frames import Frame
from iagent.workspace import Workspace

logger = logging.getLogger("iagent.live")

ANSWER_EXPIRES_S = 90.0  # an answer waits this long for a straight


@dataclass
class Options:
    source: str = "iracing"  # "iracing" or "replay"
    file: str | None = None  # replay: the .ibt
    start_at: float | None = None  # replay: session time to start from
    speed: float = 1.0
    ref: str | None = None
    voice: str = "alba"  # a Pocket TTS voice, or "silent" (log only)
    learning_laps: int = 2
    focus: bool = True
    crewchief: str = "auto"  # "auto" (detect it), "on" or "off"

    @classmethod
    def from_dict(cls, raw: dict) -> "Options":
        known = {k: raw[k] for k in cls.__dataclass_fields__ if raw.get(k) is not None}
        return cls(**known)


class SessionError(Exception):
    pass


class LiveSessions:
    def __init__(self, workspace: Path, voice_factory: Callable[[str], Voice] | None = None,
                 iracing: Callable[[], object] | None = None):
        self.workspace = workspace
        self._voice_factory = voice_factory or self._pocket
        self._iracing = iracing  # makes the live source (tests inject one)
        self._voices: dict[str, Voice] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._calls: queue.Queue[Callable[[LiveCoach, float], None]] = queue.Queue()
        self._events: list[dict] = []
        self._log_path: Path | None = None
        self.coach: LiveCoach | None = None
        self.state = "idle"  # idle, starting, waiting (for iRacing), running, stopping, error
        self.error: str | None = None
        self.options: Options | None = None
        self.session_id: str | None = None
        self._last_frame: Frame | None = None
        self._source = None
        self._ref_driver: str | None = None

    # --- control -------------------------------------------------------------------------------

    def start(self, raw: dict) -> dict:
        options = Options.from_dict(raw)
        if options.source not in ("iracing", "replay"):
            raise SessionError("source must be iracing or replay")
        if options.source == "replay" and not (options.file and Path(options.file).is_file()):
            raise SessionError(f"No such recording: {options.file}")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise SessionError("A session is already running: stop it first.")
            self._stop.clear()
            self._calls = queue.Queue()
            self._events = []
            self.coach, self.error, self.options = None, None, options
            self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._log_path = self.workspace / "sessions" / "live" / f"{self.session_id}.jsonl"
            self.state = "starting"
            self._thread = threading.Thread(target=self._run, name="live-coach", daemon=True)
            self._thread.start()
        self._log({"type": "status", "state": "starting", "options": options.__dict__})
        return self.status()

    def stop(self, wait_s: float = 5.0) -> dict:
        thread = self._thread
        if thread is not None and thread.is_alive():
            self.state = "stopping"
            self._stop.set()
            if hasattr(self._source, "stop"):
                self._source.stop()  # iRacing paused sends no frames to notice the stop with
            thread.join(timeout=wait_s)
        return self.status()

    def wait(self, timeout_s: float | None = None) -> None:
        """Until the session ends (a replay runs out). For tests and scripts."""
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)

    def say(self, text: str, kind: str = "answer") -> None:
        """Queue a line (e.g. the coach's answer to a question) for the next straight."""
        def call(coach: LiveCoach, now: float) -> None:
            coach.arbiter.say(Utterance(text, ANSWER, kind, now, now + ANSWER_EXPIRES_S))
        self._calls.put(call)

    def set_focus(self, corner: int | None) -> None:
        if self.coach is None:
            raise SessionError("No session running.")

        def call(coach: LiveCoach, now: float) -> None:
            try:
                entry = coach.set_focus(corner)
            except KeyError as e:
                self._log({"type": "error", "message": str(e)})
                return
            if entry is not None:
                entry["logged"] = True
            self._log({"type": "focus", "at": now, "by": "driver",
                       "focus": {k: v for k, v in entry.items() if k != "logged"} if entry else None})
        self._calls.put(call)

    def note_driver(self, text: str) -> dict:
        return self._log({"type": "driver", "at": self._now(), "text": text})

    def note_answer(self, text: str) -> dict:
        return self._log({"type": "answer", "at": self._now(), "text": text})

    # --- what the page reads -------------------------------------------------------------------

    def status(self) -> dict:
        coach = self.coach
        out = {
            "state": self.state,
            "error": self.error,
            "session_id": self.session_id,
            "options": self.options.__dict__ if self.options else None,
            "events": len(self._events),
        }
        if coach is not None:
            last = self._last_frame
            focus = next(({k: v for k, v in f.items() if k != "logged"} for f in reversed(coach.focus_log)
                          if f["cue"] == coach.focus), None)
            out.update({
                "track": {"key": coach.session.track_key, "name": coach.session.track_name,
                          "car": coach.session.car_key, "car_name": coach.session.car_name},
                "ref": {"lap_id": coach.plan.ref_lap_id, "lap_time": coach.plan.ref_lap_time, "driver": self._ref_driver},
                "laps_done": coach._laps_done,
                "mode": coach.mode,
                "learning": coach._laps_done < coach.settings.learning_laps,
                "focus": focus,
                "cues": [{"corners": c.corners, "text": c.text, "cued": coach._wanted(c)} for c in coach.plan.cues],
                "lap_dist": last.get("LapDist") if last else None,
                "session_time": last.session_time if last else None,
            })
        return out

    def events(self, since: int = 0) -> list[dict]:
        return self._events[since:]

    def context(self) -> dict:
        """What the coach is told about the session when the driver asks something."""
        st = self.status()
        laps = [e for e in self._events if e["type"] == "lap"][-3:]
        lines = []
        for lap in laps:
            worst = sorted(lap["corners"], key=lambda c: -c["delta_s"])[:3]
            lines.append(f"lap {lap['lap']}: {lap['lap_time']:.3f} s, gap {lap['gap_s']:+.3f} s"
                         + {"pushing": "", "moment": f" (a moment near T{lap['moment_at']})",
                            "tranquille": " (not pushing)"}[lap["pace"]] + "; most lost: "
                         + ", ".join(f"T{c['corner']} {c['delta_s']:+.2f}" for c in worst))
        if st.get("focus"):
            lines.append(f"current focus: {st['focus']['label']}")
        track = st.get("track") or {}
        return {"page": "live", "track": track.get("key"), "car": track.get("car"),
                "ref": (st.get("ref") or {}).get("lap_id"), "live": lines}

    # --- the coach thread ----------------------------------------------------------------------

    def _run(self) -> None:
        options = self.options
        try:
            voice = CapturedVoice() if options.voice == "silent" else self._voice(options.voice)
            if options.source == "replay":
                from iagent.live.sources import LIVE_CHANNELS, paced
                from iagent.telemetry.ibt import IbtSource

                src = IbtSource(options.file, channels=LIVE_CHANNELS)
                session_of = lambda: src.session  # noqa: E731
                frames = paced(src.frames(), options.speed, options.start_at)
            else:
                src = self._iracing() if self._iracing else _irsdk()
                self._source = src
                self.state = "waiting"
                while not src.connect(timeout_s=1.0):
                    if self._stop.is_set():
                        self.state = "idle"
                        return
                session_of = lambda: src.session  # noqa: E731
                frames = src.frames()
            from iagent.live import crewchief

            settings = Settings(learning_laps=options.learning_laps, focus=options.focus,
                                crewchief=crewchief.resolve(options.crewchief))
            run(self.workspace, session_of, self._frames(frames), voice, settings, options.ref, self._attach)
            self._log({"type": "status", "state": "stopped" if self._stop.is_set() else "ended"})
        except Exception as e:  # report it on the page
            logger.exception("Live session failed")
            self.error = str(e)
            self.state = "error"
            self._log({"type": "error", "message": str(e)})
            return
        self.state = "idle"

    def _frames(self, frames: Iterator[Frame]) -> Iterator[Frame]:
        for frame in frames:
            if self._stop.is_set():
                return
            self._last_frame = frame
            if self.coach is not None:
                while not self._calls.empty():
                    self._calls.get_nowait()(self.coach, frame.session_time)
            yield frame

    def _attach(self, coach: LiveCoach) -> None:
        ws = Workspace(self.workspace)
        try:
            self._ref_driver = ws.ref_meta(coach.plan.ref_lap_id).get("driver")
        finally:
            ws.close()
        self.coach = coach
        self.state = "running"
        coach.arbiter.on_event = self._line
        coach.on_lap = lambda lap: self._lap(coach, lap)
        coach.on_mode = lambda mode, now, d: self._log({"type": "pace", "mode": mode, "at": now, "lap_dist": round(d)})
        coach.on_advice = lambda advice: self._log({"type": "advice", **advice})
        coach.rules.on_fire = lambda fired: self._log({"type": "rule", **fired})
        coach.rules.on_wake = lambda wake: self._log({"type": "wake", **wake})
        self._log({"type": "status", "state": "running", "track": coach.session.track_name,
                   "car": coach.session.car_name, "ref": coach.plan.ref_lap_id,
                   "track_key": coach.session.track_key, "car_key": coach.session.car_key,
                   "ref_lap_time": coach.plan.ref_lap_time, "length_m": coach.length,
                   "source": self.options.source, "file": self.options.file,
                   "focus": coach.focus_log[-1] if coach.focus_log else None,
                   "rules": [r.id for r in coach.rules.rules], "crewchief": coach.settings.crewchief})

    def _line(self, what: str, u: Utterance, now: float) -> None:
        self._log({"type": "line", "status": what, "kind": u.kind, "text": u.text, "corner": u.corner,
                   "at": now, "note": u.note if what == "dropped" else None, "rule": u.rule})

    def _lap(self, coach: LiveCoach, lap: dict) -> None:
        self._log({"type": "lap", **lap})
        latest = coach.focus_log[-1] if coach.focus_log else None
        if latest is not None and latest.get("logged") is None:
            latest["logged"] = True
            self._log({"type": "focus", "at": lap["at"], "focus": {k: v for k, v in latest.items() if k != "logged"},
                       "by": "driver" if latest.get("manual") else "coach"})

    def _log(self, event: dict) -> dict:
        event = {"seq": len(self._events), "wall": datetime.now(timezone.utc).isoformat(), **event}
        self._events.append(event)
        if self._log_path is not None:
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a") as f:
                    f.write(json.dumps(event) + "\n")
            except OSError:
                logger.exception("Couldn't write the session log")
        return event

    def _now(self) -> float | None:
        return self._last_frame.session_time if self._last_frame else None

    def _voice(self, name: str) -> Voice:
        if name not in self._voices:
            self._voices[name] = self._voice_factory(name)
        return self._voices[name]

    def _pocket(self, name: str) -> Voice:
        from iagent.live.voice import PocketVoice

        return PocketVoice(self.workspace / "cache" / "voice", voice=name)


def _irsdk():
    from iagent.live.sources import IrsdkSource

    return IrsdkSource()


def recordings(folders: list[Path], limit: int = 50) -> list[dict]:
    """.ibt files to replay, newest first."""
    files = []
    for folder in folders:
        if folder.is_dir():
            files += list(folder.glob("*.ibt"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"path": str(p), "name": p.name, "size": p.stat().st_size,
             "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))} for p in files[:limit]]
