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

- **Pushing or not.** Pace over the last 400 m is compared with the driver's own best lap (on a
  track they haven't lapped yet, generously with the reference). Clearly slower (10%+) means
  tranquille: an out lap, a cool-down, or the few hundred metres after a moment. Then nothing is
  cued or assessed until they're back within 5%, so a moment doesn't write off the rest of the lap.

Nothing is said on pit road or off the racing surface, and nothing new while a car is alongside.

Rules the agent has activated (`iagent.live.rules`) are evaluated alongside, on the same frames
and events, and speak through the same arbiter.
"""

from collections import deque
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np
import pandas as pd

from iagent.analysis.corners import CornerMap, corner_metrics
from iagent.laps.segment import Lap, LapSegmenter
from iagent.live.cues import Cue, CuePlan
from iagent.live.expr import round5 as _round5
from iagent.live.expr import say_gap as _say_gap
from iagent.live.expr import say_time as _say_time
from iagent.live.rules import RuleEngine
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
    focus: bool = True  # after the learning laps, coach one cue at a time
    focus_min_s: float = 0.15  # a cue must lose at least this (mean of the last two laps) to be the focus
    others_s: float = 0.25  # with a focus, other corners speak up only past this loss (or an off)
    pace_window_m: float = 400.0  # pace is judged over this much track
    push_ratio: float = 1.05  # back to pushing at this pace or better (vs own best)
    tranquille_ratio: float = 1.10  # tranquille at this pace or slower
    new_track_ratio: float = 1.25  # with no lap of their own yet, vs the reference


@dataclass
class CornerResult:
    corner: int
    delta_s: float
    advice: str | None  # said after the corner
    hint: str | None  # added to the corner's cue next lap
    struggling: bool
    metrics: dict | None = None  # this lap's corner metrics (what rules see)


@dataclass
class _LapState:
    rows: list[tuple[float, ...]] = field(default_factory=list)  # LIVE_CHANNELS, since the line
    assessed: set[int] = field(default_factory=set)
    results: list[CornerResult] = field(default_factory=list)
    feedback: int = 0
    clean: bool = True  # started at the line, no pit road
    frames: int = 0
    pushing_frames: int = 0
    slow: list[list[float]] = field(default_factory=list)  # [from_m, to_m] stretches not pushing


class LiveCoach:
    def __init__(self, session: SessionInfo, plan: CuePlan, cmap: CornerMap, ref_grid: pd.DataFrame,
                 arbiter: Arbiter, settings: Settings | None = None, own_best: pd.DataFrame | None = None,
                 carried_focus: int | None = None, rules: RuleEngine | None = None):
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
        self.on_mode: Callable[[str, float, float], None] | None = None  # (mode, session time, LapDist)
        self.on_advice: Callable[[dict], None] | None = None  # advice for a corner (said, or a hint)
        # Pace: the driver's own best lap (distance, elapsed time), or the reference until they have one.
        self._pace_d, self._pace_t, self._pace_lap = self._ref_d, self._ref_t, None
        self._pace_own = own_best is not None
        if own_best is not None:
            self._pace_d = own_best["LapDist"].to_numpy(dtype=float)
            self._pace_t = own_best["lap_time_s"].to_numpy(dtype=float)
            self._pace_lap = float(self._pace_t[-1])
        self._trail: deque[tuple[float, float]] = deque()  # (distance run, session time)
        self._run_m = 0.0
        self._prev_d: float | None = None
        self._frame_no = 0
        self.mode = "pushing"  # or "tranquille"
        self._carried = False
        if carried_focus is not None and self.plan.cue_for(carried_focus) is not None:
            # From the last session's plan: where we start, re-checked after two pushing laps.
            cue = self.plan.cue_for(carried_focus)
            self.focus, self._focus_heard, self._carried = cue.corner, False, True
            self.focus_log.append({"cue": cue.corner, "corners": cue.corners, "label": self._label(cue),
                                   "set_lap": 0, "done_lap": None, "manual": False, "carried": True})
        self.results: list[list[CornerResult]] = []  # per completed lap
        self.rules = rules
        if rules is not None:
            rules.attach(self)

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
            if self.rules is not None:
                self.rules.frame(frame, now, on_track=False)
            self.arbiter.clear(("approach", "feedback"), now, "you were in the pits or off the racing surface")
            self.arbiter.tick(now, hold=True)
            return
        self._lap.rows.append(tuple(float(frame.get(c, np.nan)) for c in LIVE_CHANNELS))
        self._track_pace(now, d)
        if self.rules is not None:
            self.rules.frame(frame, now, on_track=True)
        if self.mode == "tranquille":
            self._assess(now, d)  # only an off-track hint for next lap comes out of this
            self.arbiter.tick(now, hold=True)  # no cues, no feedback until pushing again
            return

        due = self._approach(now, d, max(v, 5.0))
        self._assess(now, d)
        hold = (frame.get("CarLeftRight") or 0) >= CAR_ALONGSIDE
        # Between a cue and the end of its corners only cues are said: feedback waits for a straight.
        self.arbiter.tick(now, hold=hold, free_for_s=0.0 if self._in_corner(d) else due)

    # --- pushing or not ------------------------------------------------------------------------

    def _track_pace(self, now: float, d: float) -> None:
        if self._prev_d is not None:
            step = d - self._prev_d
            if step < -self.length / 2:
                step += self.length  # crossed the line
            if abs(step) > 200.0:  # a reset or tow: start judging afresh
                self._trail.clear()
                step = 0.0
            self._run_m += max(step, 0.0)
        self._prev_d = d
        self._trail.append((self._run_m, now))
        window = self.settings.pace_window_m
        while len(self._trail) > 2 and self._trail[1][0] <= self._run_m - window:
            self._trail.popleft()
        self._lap.frames += 1
        self._frame_no += 1
        if self._frame_no % 6 == 0 and self._run_m - self._trail[0][0] >= window * 0.95:
            ratio = self.pace_ratio(d)
            s = self.settings
            slow = s.tranquille_ratio if self._pace_own else s.new_track_ratio
            back = s.push_ratio if self._pace_own else s.new_track_ratio - 0.05
            if self.mode == "pushing" and ratio >= slow:
                self._set_mode("tranquille", now, d)
            elif self.mode == "tranquille" and ratio <= back:
                self._set_mode("pushing", now, d)
        if self.mode == "pushing":
            self._lap.pushing_frames += 1

    def pace_ratio(self, d: float) -> float:
        """Time over the last window against the pace lap over the same stretch (1.0: as fast)."""
        (m0, t0), (m1, t1) = self._trail[0], self._trail[-1]
        span = m1 - m0
        lap = self._pace_lap or float(self._pace_t[-1])
        a, b = np.interp((d - span) % self.length, self._pace_d, self._pace_t), np.interp(d, self._pace_d, self._pace_t)
        best = float((b - a) % lap) or 1e-6
        return (t1 - t0) / best

    def _set_mode(self, mode: str, now: float, d: float) -> None:
        self.mode = mode
        if mode == "tranquille":
            self._lap.slow.append([round(max(0.0, d - self.settings.pace_window_m / 2)), round(d)])
            self.arbiter.clear(("approach", "feedback", "focus"), now, "you weren't pushing")
        elif self._lap.slow:
            self._lap.slow[-1][1] = round(d)
        if self.on_mode is not None:
            self.on_mode(mode, now, d)
        if self.rules is not None:
            self.rules.pace(mode, now)

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
        # Only pushing teaches anything: not out laps, cool-downs or the stretch after a moment.
        at_pace = self._lap.clean and self.mode == "pushing"
        for c in self.cmap.corners:
            at = min(c.exit_m + self.settings.feedback_after_m, c.segment_end_m - 1.0)
            if c.id in self._lap.assessed or not at <= d <= c.segment_end_m + 1.0:
                continue
            self._lap.assessed.add(c.id)
            result = self._corner_result(c.id, d)
            if result is None:
                continue
            if self.rules is not None:
                self.rules.corner_exit(result, at_pace, now)
            if not at_pace:
                # Not pushing: the numbers don't count, but going off is still worth a word next lap.
                if result.hint and "off track" in (result.advice or ""):
                    self._hints[c.id] = result.hint
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
            if (result.advice or result.hint) and self.on_advice is not None:
                self.on_advice({"corner": c.id, "lap": self._laps_done + 1, "delta_s": result.delta_s,
                                "advice": result.advice, "hint": result.hint, "at": now})
            cue = self.plan.cue_for(c.id)
            said = any(r.advice for r in self._lap.results[:-1] if cue and r.corner in cue.corners)
            if result.advice and not said and self._lap.feedback < s.feedback_per_lap:
                self._lap.feedback += 1
                self.arbiter.say(Utterance(result.advice, FEEDBACK, "feedback", now, now + s.feedback_expires_s, c.id))

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
        return CornerResult(corner_id, round(float(delta), 3), advice, hint, struggling, mine)

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
        if self.rules is not None:
            self.rules.line_crossed()
        if not state.clean or not lap.complete:
            return  # out lap or a lap joined midway: it doesn't count towards learning
        self._laps_done += 1
        self.results.append(state.results)
        ref_time = self.plan.ref_lap_time
        if self.mode == "tranquille" and state.slow:
            state.slow[-1][1] = round(self.length)  # still slow at the line
        pace, moment_at = self._classify(state)
        # Focus moves on what the pushing parts of the lap showed (most of the corners at least).
        enough = len(state.results) >= max(1, len(self.cmap.corners) // 2)
        news = self._update_focus(state.results) if enough else None
        best_before = self._pace_lap
        if pace == "pushing" and lap.lap_time and (self._pace_lap is None or lap.lap_time < self._pace_lap):
            self._new_best(lap)
        # Both wait for the first straight with room for them, up to most of a lap.
        wait = 0.6 * (self.plan.ref_lap_time or lap.lap_time or 60.0)
        if self.settings.summary and lap.lap_time is not None and pace != "tranquille":
            self.arbiter.say(Utterance(self._summary(lap, state.results, moment_at), SUMMARY, "summary", now, now + wait))
        if news:
            self.arbiter.say(Utterance(news, FEEDBACK, "focus", now, now + wait, self.focus))
        info = {
            "lap": self._laps_done, "lap_time": lap.lap_time, "at": now,
            "gap_s": round(lap.lap_time - ref_time, 3) if lap.lap_time and ref_time else None,
            "pace": pace,  # "pushing", "moment" or "tranquille"
            "pushing_share": round(state.pushing_frames / state.frames, 2) if state.frames else 0.0,
            "slow": state.slow,  # [from_m, to_m] stretches not pushing
            "moment_at": moment_at,
            "corners": [{"corner": r.corner, "delta_s": r.delta_s} for r in state.results],
            "focus": self.focus,
        }
        if self.on_lap is not None:
            self.on_lap(info)
        if self.rules is not None:
            self.rules.lap(info, best_before, now)

    def _classify(self, state: "_LapState") -> tuple[str, int | None]:
        """pushing (all the way), moment (mostly pushing, one slow stretch: returns the corner where
        it started), or tranquille (mostly not pushing)."""
        share = state.pushing_frames / state.frames if state.frames else 0.0
        if not state.slow:
            return "pushing", None
        if share >= 0.6:  # mostly pushing: one moment (a spin and its recovery can run over a km)
            where = max(state.slow, key=lambda ab: ab[1] - ab[0])[0]
            corner = min(self.cmap.corners, key=lambda c: abs(c.apex_m - where))
            return "moment", corner.id
        return "tranquille", None

    def _new_best(self, lap: Lap) -> None:
        """A faster pushing lap becomes the pace to judge pushing by."""
        dist = np.maximum.accumulate(lap.frames["LapDist"].to_numpy(dtype=float))
        self._pace_d, self._pace_t = dist, lap.frames["lap_time_s"].to_numpy(dtype=float)
        self._pace_lap, self._pace_own = lap.lap_time, True

    # --- focus ---------------------------------------------------------------------------------

    def _update_focus(self, results: list["CornerResult"]) -> str | None:
        """After a lap at pace: is the focus done, and what's next? Returns what to announce."""
        s = self.settings
        losses: dict[int, float] = {}
        judged: dict[int, int] = {}
        for r in results:
            cue = self.plan.cue_for(r.corner)
            if cue is not None and r.delta_s <= s.incident_s:
                losses[cue.corner] = losses.get(cue.corner, 0.0) + r.delta_s
                judged[cue.corner] = judged.get(cue.corner, 0) + 1
        # A cue counts only when all its corners were judged (pushing through all of them).
        losses = {k: v for k, v in losses.items() if judged[k] == len(self.plan.cue_for(k).corners)}
        self._cue_losses.append(losses)
        if not s.focus or self._laps_done < s.learning_laps:
            return None
        news = []
        if self._carried and not self.focus_manual and self.focus is not None:
            replaced = self._recheck_carried()
            if replaced:
                news.append(replaced)
        if self.focus is not None:
            recent = [lap[self.focus] for lap in self._cue_losses if self.focus in lap][-2:]
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

    def _recheck_carried(self) -> str | None:
        """After two pushing laps, a focus carried over from last time stays only if it's still
        worth it: not yet sorted, and nothing else losing clearly more."""
        s = self.settings
        values = [lap[self.focus] for lap in self._cue_losses if self.focus in lap]
        if len(values) < 2:
            return None
        self._carried = False
        entry = next(f for f in reversed(self.focus_log) if f["cue"] == self.focus)
        mine = sum(values[-2:]) / 2
        if mine < s.focus_min_s:
            entry["done_lap"] = self._laps_done
            self.focus = None
            return f"{entry['label']} is fine now."
        best = self._pick_focus()
        if best is not None and best != self.focus:
            other = [lap[best] for lap in self._cue_losses[-2:] if best in lap]
            if other and sum(other) / len(other) > mine + 0.1:
                entry["done_lap"] = self._laps_done
                entry["replaced"] = True
                self.focus = None
        return None

    def _pick_focus(self) -> int | None:
        s = self.settings
        recent = self._cue_losses[-2:]
        done = {f["cue"] for f in self.focus_log if f["done_lap"] is not None}
        mean = {}
        for cue in self.plan.cues:
            values = [lap[cue.corner] for lap in recent if cue.corner in lap]  # pushing laps only
            if values:
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

    def _summary(self, lap: Lap, results: list[CornerResult], moment_at: int | None = None) -> str:
        """Short enough (~3 s) to fit the straights of a busy track: "1 33.9, 3 seconds down. Worst: Turn 6." """
        ref_time = self.plan.ref_lap_time
        text = _say_time(lap.lap_time)
        if moment_at is not None:  # the gap means nothing: say where it went
            c = self.cmap.get(moment_at)
            return f"{text}. Lost it at {c.name or f'Turn {c.id}'}."
        if not ref_time:
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

