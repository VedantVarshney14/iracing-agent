"""The live coach: turns telemetry frames into spoken cues for learning a track.

Per frame, no model involved:

- **Approach cues.** Each corner's cue starts so it finishes at the cue's target (the reference
  brake point, or just before turn-in) at the car's current speed, plus a little lead.
- **Thinning.** The first `learning_laps` laps cue every corner. After that only the corners that
  went badly on the previous lap are cued, so the coach goes quiet as the driver learns.
- **Corner feedback.** Shortly after each corner's exit its metrics are computed with the same
  code the lap review uses and compared with the reference lap; when the corner cost time, the
  clearest cause is said on the next straight ("braked 20 metres early"), and a short hint is
  added to that corner's cue next lap ("Brake later than last lap."), which is when it's useful.
- **Focus.** After the learning laps one cue becomes the focus: the corners losing the most time
  over the last two laps at pace. It's cued every lap; other corners speak up only for a big loss
  or an off. Once the focus is within `loss_s` on two laps at pace it's done and the next one is
  picked. Changes are announced in the lap summary. (`set_focus` overrides it.)
- **Lap summary** at the line: lap time, gap to the reference, the corner that cost the most.

Nothing is said on pit road or off the racing surface, and nothing new while a car is alongside.
"""

from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np
import pandas as pd

from iagent.analysis.corners import CornerMap, corner_metrics
from iagent.laps.segment import Lap, LapSegmenter
from iagent.live.cues import Cue, CuePlan
from iagent.live.speech import APPROACH, FEEDBACK, SUMMARY, Arbiter, Utterance
from iagent.telemetry.frames import SURFACE_OFF_TRACK, SURFACE_ON_TRACK, Frame
from iagent.telemetry.session import SessionInfo

LIVE_CHANNELS = ("LapDist", "Speed", "Brake", "Throttle", "Gear", "PlayerTrackSurface", "SessionTime")
# iRacing's CarLeftRight: 0 off, 1 clear, 2+ a car (or cars) alongside.
CAR_ALONGSIDE = 2


@dataclass
class Settings:
    learning_laps: int = 2  # laps with every corner cued
    lead_s: float = 0.8  # finish a cue this long before its target
    feedback_after_m: float = 60.0  # assess a corner this far past its exit
    loss_s: float = 0.08  # a corner that cost at least this much gets feedback / stays cued
    brake_m: float = 15.0  # brake point differences smaller than this aren't mentioned
    min_speed_kph: float = 5.0
    throttle_m: float = 25.0
    off_track_m: float = 5.0
    feedback_per_lap: int = 3
    feedback_expires_s: float = 8.0
    incident_s: float = 1.5  # a corner that cost more than this was an incident, not technique
    summary: bool = True
    off_pace: float = 0.07  # laps this far off the reference get no gap or worst corner
    focus: bool = True  # after the learning laps, coach one cue at a time
    focus_min_s: float = 0.15  # a cue must lose at least this (mean of the last two laps) to be the focus
    others_s: float = 0.25  # with a focus, other corners speak up only past this loss (or an off)


@dataclass
class CornerResult:
    corner: int
    delta_s: float
    advice: str | None  # said after the corner
    hint: str | None  # added to the corner's cue next lap
    struggling: bool


@dataclass
class _LapState:
    rows: list[tuple[float, ...]] = field(default_factory=list)  # LIVE_CHANNELS, since the line
    assessed: set[int] = field(default_factory=set)
    results: list[CornerResult] = field(default_factory=list)
    feedback: int = 0
    clean: bool = True  # started at the line, no pit road


class LiveCoach:
    def __init__(self, session: SessionInfo, plan: CuePlan, cmap: CornerMap, ref_grid: pd.DataFrame,
                 arbiter: Arbiter, settings: Settings | None = None):
        self.session = session
        self.plan = plan
        self.cmap = cmap
        self.arbiter = arbiter
        self.settings = settings or Settings()
        self.length = plan.length_m
        self._ref_d = ref_grid["LapDist"].to_numpy(dtype=float)
        self._ref_t = ref_grid["lap_time_s"].to_numpy(dtype=float)
        self._ref_metrics = {m["corner"]: m for m in plan.ref_metrics}
        self._segmenter = LapSegmenter(session)
        self._lap = _LapState(clean=False)  # joined mid-lap
        self._laps_done = 0
        self._last_cued: dict[int, float] = {}  # cue corner -> session time it was said
        self._struggling: set[int] = set()  # corners cued after the learning laps
        self._hints: dict[int, str] = {}  # corner -> hint for its next cue
        self._big_trouble: set[int] = set()  # corners worth a cue even with a focus set
        self._cue_losses: list[dict[int, float]] = []  # per lap at pace: cue -> time lost (s)
        self.focus: int | None = None  # the focus cue (its first corner)
        self.focus_manual = False
        self._focus_heard = True  # the focus cue says "Focus." the first time after a change
        self.focus_log: list[dict] = []  # {"cue", "label", "set_lap", "done_lap"}
        self.on_lap: Callable[[dict], None] | None = None  # called with each counted lap
        self.results: list[list[CornerResult]] = []  # per completed lap

    # --- per frame -----------------------------------------------------------------------------

    def push(self, frame: Frame) -> None:
        now = frame.session_time
        lap = self._segmenter.push(frame)
        if lap is not None:
            self._lap_done(lap, now)

        d, v = frame.get("LapDist"), frame.get("Speed")
        surface = frame.get("PlayerTrackSurface", SURFACE_ON_TRACK)
        on_track = surface in (SURFACE_ON_TRACK, SURFACE_OFF_TRACK) and not frame.get("OnPitRoad", 0)
        if not on_track or d is None or v is None:
            self._lap.clean = False
            self.arbiter.clear(("approach", "feedback"), now, "you were in the pits or off the racing surface")
            self.arbiter.tick(now, hold=True)
            return
        self._lap.rows.append(tuple(float(frame.get(c, np.nan)) for c in LIVE_CHANNELS))

        due = self._approach(now, d, max(v, 5.0))
        self._assess(now, d)
        hold = (frame.get("CarLeftRight") or 0) >= CAR_ALONGSIDE
        # Between a cue and the end of its corners only cues are said: feedback waits for a straight.
        self.arbiter.tick(now, hold=hold, free_for_s=0.0 if self._in_corner(d) else due)

    def _in_corner(self, d: float) -> bool:
        for cue in self.plan.cues:
            end = self.cmap.get(cue.corners[-1]).exit_m
            start = cue.target_m - 100.0
            if start <= d <= end or (start < 0 and d >= start % self.length):
                return True
        return False

    def _approach(self, now: float, d: float, v: float) -> float:
        """Queue cues whose start point has come; returns the time until the next one is due."""
        due = float("inf")
        for cue in self.plan.cues:
            if not self._wanted(cue):
                continue
            to_target = (cue.target_m - d) % self.length
            text = self._cue_text(cue)
            length_s = self.arbiter.voice.duration(text) or Utterance(text, 0, "", 0, 0).length_s
            start_in = to_target / v - length_s - self.settings.lead_s
            if start_in > 0:
                # For planning other speech, time the run to the cue as the reference lap did: the
                # current speed underestimates it badly when accelerating out of a slow corner.
                due = min(due, min(start_in, self._ref_time_between(d, cue.target_m) - length_s - self.settings.lead_s))
                continue
            if now - self._last_cued.get(cue.corner, -1e9) < 20.0 or to_target > self.length / 2:
                continue  # said already, or the target is behind us
            self._last_cued[cue.corner] = now
            if cue.corner == self.focus:
                self._focus_heard = True
            for c in cue.corners:
                self._hints.pop(c, None)
            # Not worth starting once it can't finish before the target.
            self.arbiter.say(Utterance(text, APPROACH, "approach", now, now + to_target / v - length_s,
                                       cue.corner, length_s))
        return due

    def _ref_time_between(self, a: float, b: float) -> float:
        ta, tb = np.interp(a, self._ref_d, self._ref_t), np.interp(b, self._ref_d, self._ref_t)
        lap = self.plan.ref_lap_time or float(self._ref_t[-1])
        return float((tb - ta) % lap)

    def _cue_text(self, cue: Cue) -> str:
        text = self._hinted(cue)
        if cue.corner == self.focus and not self._focus_heard:
            return f"Focus. {text}"
        return text

    def _hinted(self, cue: Cue) -> str:
        corner = next((c for c in cue.corners if c in self._hints), None)
        if corner is None:
            return cue.text
        hint = self._hints[corner]
        if corner != cue.corner:  # about the second corner of the cue: say which
            c = self.cmap.get(corner)
            hint = f"{c.name or f'Turn {c.id}'}: {hint[0].lower()}{hint[1:]}"
        return f"{cue.text} {hint}"

    def _wanted(self, cue: Cue) -> bool:
        if self._laps_done < self.settings.learning_laps:
            return True
        if self.focus is not None:
            return cue.corner == self.focus or any(c in self._big_trouble or c in self._hints for c in cue.corners)
        return any(c in self._struggling or c in self._hints for c in cue.corners)

    def _quiet(self, corner: int, result: "CornerResult") -> bool:
        """With a focus set, keep a corner's feedback and hints to itself unless it was bad."""
        if self.focus is None:
            return False
        cue = self.plan.cue_for(corner)
        if cue is not None and cue.corner == self.focus:
            return False
        return corner not in self._big_trouble

    # --- after each corner ---------------------------------------------------------------------

    def _assess(self, now: float, d: float) -> None:
        # Only laps driven at pace teach anything: not out laps, cool-downs or laps after a moment.
        at_pace = self._lap.clean and self._at_pace(now, d)
        for c in self.cmap.corners:
            at = min(c.exit_m + self.settings.feedback_after_m, c.segment_end_m - 1.0)
            if c.id in self._lap.assessed or not at <= d <= c.segment_end_m + 1.0:
                continue
            self._lap.assessed.add(c.id)
            result = self._corner_result(c.id, d)
            if result is None or not at_pace:
                continue
            self._lap.results.append(result)
            if result.struggling:
                self._struggling.add(c.id)
            else:
                self._struggling.discard(c.id)
            s = self.settings
            if result.delta_s >= s.others_s or "off track" in (result.advice or ""):
                self._big_trouble.add(c.id)
            else:
                self._big_trouble.discard(c.id)
            if self._quiet(c.id, result):
                continue
            if result.hint:
                self._hints[c.id] = result.hint
            cue = self.plan.cue_for(c.id)
            said = any(r.advice for r in self._lap.results[:-1] if cue and r.corner in cue.corners)
            if result.advice and not said and self._lap.feedback < s.feedback_per_lap:
                self._lap.feedback += 1
                self.arbiter.say(Utterance(result.advice, FEEDBACK, "feedback", now, now + s.feedback_expires_s, c.id))

    def _at_pace(self, now: float, d: float) -> bool:
        if not self._lap.rows:
            return False
        elapsed = now - self._lap.rows[0][LIVE_CHANNELS.index("SessionTime")]
        ref = float(np.interp(d, self._ref_d, self._ref_t))
        return elapsed <= ref * (1 + self.settings.off_pace) + 1.0

    def _corner_result(self, corner_id: int, d: float) -> CornerResult | None:
        c = self.cmap.get(corner_id)
        ref = self._ref_metrics.get(corner_id)
        lo = max(0.0, c.segment_start_m - 60.0)
        grid = self._grid(lo, d)
        if grid is None or ref is None or ref.get("missing"):
            return None
        mine = corner_metrics(grid, None, replace(self.cmap, corners=[c]))[0]
        if mine.get("missing"):
            return None
        t = grid["lap_time_s"].to_numpy()
        dist = grid["LapDist"].to_numpy()
        start = max(c.segment_start_m, float(dist[0]))
        delta = (np.interp(d, dist, t) - np.interp(start, dist, t)) - (
            np.interp(d, self._ref_d, self._ref_t) - np.interp(start, self._ref_d, self._ref_t))
        advice, hint = self._advice(c.name or f"Turn {c.id}", mine, ref, float(delta))
        s = self.settings
        struggling = delta >= s.loss_s or (mine.get("off_track_m") or 0) >= s.off_track_m
        if delta > s.incident_s:
            advice = hint = None  # a spin or a moment: the numbers describe the incident, not the technique
        return CornerResult(corner_id, round(float(delta), 3), advice, hint, struggling)

    def _advice(self, name: str, mine: dict, ref: dict, delta: float) -> tuple[str | None, str | None]:
        """The clearest cause of a corner that cost time, in the driver's terms: what to say after
        the corner, and the hint for its cue next lap."""
        s = self.settings
        if (mine.get("off_track_m") or 0) >= s.off_track_m:
            return f"{name}: you ran off track. Brake a touch earlier and tidy the entry.", "Tidy entry, you ran wide last lap."
        if delta < s.loss_s:
            return None, None
        bm, rb = mine.get("brake_m"), ref.get("brake_m")
        if bm is not None and rb is not None:
            diff = bm - rb
            if diff <= -s.brake_m:
                return f"{name}: braked {_round5(-diff)} metres early. Brake later.", "Brake later than last lap."
            if diff >= s.brake_m and (mine.get("min_speed_kph") or 0) < (ref.get("min_speed_kph") or 0):
                return f"{name}: braked {_round5(diff)} metres late and lost the exit.", "Brake a little earlier than last lap."
        if bm is not None and rb is None:
            lift = (ref.get("min_throttle") or 1) < 0.9
            return (f"{name}: no need to brake there." + (" A lift is enough." if lift else ""),
                    "Just a lift, no brakes." if lift else "Stay off the brakes.")
        dv = _diff(mine.get("min_speed_kph"), ref.get("min_speed_kph"))
        if dv is not None and dv <= -s.min_speed_kph:
            return f"{name}: {round(-dv)} kilometres an hour slower at the apex. Carry more speed in.", "Carry more speed in."
        dt = _diff(mine.get("full_throttle_m"), ref.get("full_throttle_m"))
        if dt is not None and dt >= s.throttle_m:
            return (f"{name}: full throttle {_round5(dt)} metres later than the reference. Get on it earlier.",
                    "Earlier on the throttle.")
        return None, None

    def _grid(self, lo: float, hi: float) -> pd.DataFrame | None:
        """This lap from `lo` to `hi` on a 1 m grid, or None if it wasn't all driven."""
        if not self._lap.rows:
            return None
        raw = np.array(self._lap.rows, dtype=float)
        dist = np.maximum.accumulate(raw[:, 0])
        if dist[0] > lo + 5 or dist[-1] < hi - 5:
            return None
        grid = np.arange(lo, hi, 1.0)
        out = {"LapDist": grid}
        for i, name in enumerate(LIVE_CHANNELS[1:], start=1):
            col = np.interp(grid, dist, raw[:, i])
            out["lap_time_s" if name == "SessionTime" else name] = col
        out["Gear"] = np.round(out["Gear"])
        out["PlayerTrackSurface"] = np.round(out["PlayerTrackSurface"])
        return pd.DataFrame(out)

    # --- at the line ---------------------------------------------------------------------------

    def _lap_done(self, lap: Lap, now: float) -> None:
        state = self._lap
        self._lap = _LapState()
        self._last_cued = {k: t for k, t in self._last_cued.items() if now - t < 20.0}
        if not state.clean or not lap.complete:
            return  # out lap or a lap joined midway: it doesn't count towards learning
        self._laps_done += 1
        self.results.append(state.results)
        ref_time = self.plan.ref_lap_time
        at_pace = lap.lap_time is not None and (not ref_time or lap.lap_time <= ref_time * (1 + self.settings.off_pace))
        news = self._update_focus(state.results) if at_pace else None
        # Both wait for the first straight with room for them, up to most of a lap.
        wait = 0.6 * (self.plan.ref_lap_time or lap.lap_time or 60.0)
        if self.settings.summary and lap.lap_time is not None:
            self.arbiter.say(Utterance(self._summary(lap, state.results), SUMMARY, "summary", now, now + wait))
        if news:
            self.arbiter.say(Utterance(news, FEEDBACK, "focus", now, now + wait, self.focus))
        if self.on_lap is not None:
            self.on_lap({
                "lap": self._laps_done, "lap_time": lap.lap_time, "at": now,
                "gap_s": round(lap.lap_time - ref_time, 3) if lap.lap_time and ref_time else None,
                "at_pace": at_pace,
                "corners": [{"corner": r.corner, "delta_s": r.delta_s} for r in state.results],
                "focus": self.focus,
            })

    # --- focus ---------------------------------------------------------------------------------

    def _update_focus(self, results: list["CornerResult"]) -> str | None:
        """After a lap at pace: is the focus done, and what's next? Returns what to announce."""
        s = self.settings
        losses: dict[int, float] = {}
        for r in results:
            cue = self.plan.cue_for(r.corner)
            if cue is not None and r.delta_s <= s.incident_s:
                losses[cue.corner] = losses.get(cue.corner, 0.0) + r.delta_s
        self._cue_losses.append(losses)
        if not s.focus or self._laps_done < s.learning_laps:
            return None
        news = []
        if self.focus is not None:
            recent = [lap.get(self.focus) for lap in self._cue_losses[-2:]]
            entry = next(f for f in reversed(self.focus_log) if f["cue"] == self.focus)
            if len(recent) == 2 and all(x is not None and x < s.loss_s for x in recent) and self._laps_done > entry["set_lap"]:
                entry["done_lap"] = self._laps_done
                news.append(f"{entry['label']} sorted.")
                self.focus, self.focus_manual = None, False
        if self.focus is None:
            pick = self._pick_focus()
            if pick is not None:
                cue = self.plan.cue_for(pick)
                label = self._label(cue)
                self.focus = pick
                self._focus_heard = False
                self.focus_log.append({"cue": pick, "corners": cue.corners, "label": label,
                                       "set_lap": self._laps_done, "done_lap": None, "manual": False})
                hint = next((self._hints[c] for c in cue.corners if c in self._hints), None)
                news.append(f"Focus now: {label}." + (f" {hint}" if hint else ""))
        return " ".join(news) or None

    def _pick_focus(self) -> int | None:
        s = self.settings
        recent = self._cue_losses[-2:]
        done = {f["cue"] for f in self.focus_log if f["done_lap"] is not None}
        mean = {}
        for cue in self.plan.cues:
            values = [lap.get(cue.corner) for lap in recent]
            if values and all(v is not None for v in values):
                mean[cue.corner] = sum(values) / len(values)
        candidates = {k: v for k, v in mean.items() if v >= s.focus_min_s and k not in done}
        return max(candidates, key=candidates.get) if candidates else None

    def set_focus(self, corner: int | None) -> dict | None:
        """The driver (or coach) picks the focus: the cue covering CORNER, or None to let the
        coach pick again after the next lap."""
        if corner is None:
            self.focus, self.focus_manual = None, False
            return None
        cue = self.plan.cue_for(corner)
        if cue is None:
            raise KeyError(f"No cue covers T{corner}.")
        self.focus, self.focus_manual = cue.corner, True
        self._focus_heard = False
        entry = {"cue": cue.corner, "corners": cue.corners, "label": self._label(cue),
                 "set_lap": self._laps_done, "done_lap": None, "manual": True}
        self.focus_log.append(entry)
        return entry

    def _label(self, cue: Cue) -> str:
        names = [self.cmap.get(c).name for c in cue.corners]
        if all(names):
            return " and ".join(names)
        if len(cue.corners) == 1:
            return names[0] or f"Turn {cue.corner}"
        return f"Turns {cue.corners[0]} and {cue.corners[-1]}"

    def _summary(self, lap: Lap, results: list[CornerResult]) -> str:
        """Short enough (~3 s) to fit the straights of a busy track: "1 33.9, 3 seconds down. Worst: Turn 6." """
        ref_time = self.plan.ref_lap_time
        text = _say_time(lap.lap_time)
        if not ref_time or lap.lap_time > ref_time * (1 + self.settings.off_pace):
            return text + "."
        gap = lap.lap_time - ref_time
        text += f", {_say_gap(gap)} {'down' if gap > 0 else 'up'}."
        worst = max((r for r in results if r.delta_s <= self.settings.incident_s), key=lambda r: r.delta_s, default=None)
        focus = self.plan.cue_for(self.focus).corners if self.focus is not None else []
        if worst is not None and worst.delta_s >= self.settings.loss_s and worst.corner not in focus:
            c = self.cmap.get(worst.corner)
            text += f" Worst: {c.name or f'Turn {c.id}'}."
        return text

    def finish(self, now: float) -> None:
        """End of the stream: let anything queued play out (for replays and tests)."""
        end = now + 30.0
        t = now
        while self.arbiter.queue and t < end:
            self.arbiter.tick(t)
            t += 0.1


def _diff(a, b):
    return None if a is None or b is None else a - b


def _round5(x: float) -> int:
    return max(5, int(round(x / 5.0)) * 5)


def _say_time(s: float) -> str:
    minutes, seconds = divmod(s, 60)
    return f"{int(minutes)} {seconds:04.1f}" if minutes else f"{seconds:.1f}"


def _say_gap(gap: float) -> str:
    gap = abs(gap)
    if gap < 1.0:
        tenths = max(1, round(gap * 10))
        return f"{tenths} tenth{'s' if tenths != 1 else ''}"
    whole = round(gap, 1)
    return f"{whole:.0f} seconds" if whole == int(whole) else f"{whole:.1f} seconds"
