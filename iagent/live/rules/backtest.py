"""Backtesting rules before they go live.

A backtest replays recorded sessions through the *same* live coach and rule engine (with a
captured voice instead of a speaker): where each rule would have fired, what it would have said,
and whether the line would actually have been said or was dropped (and why).
"""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

from iagent.laps.tracks import load_track_info
from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import plan_for
from iagent.live.events import Line
from iagent.live.rules import Rule, RuleStats, check_against_map
from iagent.live.rules.store import load_rules, save_rule
from iagent.live.speech import CapturedVoice
from iagent.telemetry.frames import Frame
from iagent.telemetry.session import SessionInfo
from iagent.workspace import Workspace, WorkspaceError


def sessions_for(ws: Workspace, track: str, car: str) -> list[str]:
    """The driver's recorded sessions on this track and car, oldest first."""
    if not (ws.root / "index.sqlite").exists():
        return []
    seen: dict[str, None] = {}
    for r in ws.store().list(track=track, car=car):
        seen.setdefault(r.session_id, None)
    return list(seen)


def session_frames(
    ws: Workspace, session_id: str, track: str, car: str
) -> tuple[SessionInfo, Iterator[Frame]]:
    """A stored session's raw samples, in order, as live frames."""
    laps = sorted(
        ws.store().list(track=track, car=car, session_id=session_id),
        key=lambda r: r.seq,
    )
    if not laps:
        raise WorkspaceError(f"No laps of session {session_id!r} on {track} / {car}.")
    info = load_track_info(ws.root, track) or {}
    length = info.get("length_m") or ws.corner_map(track).length_m
    session = SessionInfo(
        track_name=info.get("name") or laps[0].track,
        track_length_m=float(length),
        car_name=(info.get("cars", {}).get(car) or {}).get("name") or laps[0].car,
        track_id=info.get("track_id"),
        session_id=session_id,
        track_code=track,
        track_config=info.get("config"),
        car_path=car,
    )

    def frames() -> Iterator[Frame]:
        last_t = -np.inf
        for rec in laps:
            raw = ws.store().load(rec.lap_id, grid=False)
            cols = [c for c in raw.columns if c != "lap_time_s"]
            for row in raw[cols].itertuples(index=False):
                values = {c: float(v) for c, v in zip(cols, row) if v == v}  # skip NaN
                t = values.get("SessionTime")
                if t is None or t <= last_t:
                    continue
                last_t = t
                yield Frame(t, values)

    return session, frames()


def backtest(
    root: Path,
    rules: list[Rule],
    track: str,
    car: str,
    sessions: list[str] | None = None,
    ibt: list[Path] | None = None,
    last: int = 3,
    with_active: bool = True,
    settings: Settings | None = None,
) -> dict:
    """Replay recordings through the live coach with RULES (plus the other active rules, which
    compete for the same voice) and report what each of RULES would have done."""
    testing = {r.id for r in rules}
    others = (
        [r for r in load_rules(root, track, car, "active") if r.id not in testing]
        if with_active
        else []
    )
    ws = Workspace(root)
    runs = []
    try:
        cmap = ws.corner_map(track)
        for rule in rules:
            check_against_map(rule, cmap)
        plan = plan_for(ws, track, car)
        sources: list[tuple[str, SessionInfo, Iterator[Frame]]] = []
        for path in ibt or []:
            from iagent.live.run import LIVE_CHANNELS
            from iagent.telemetry.ibt import IbtSource

            src = IbtSource(path, channels=LIVE_CHANNELS)
            if (src.session.track_key, src.session.car_key) != (track, car):
                raise WorkspaceError(
                    f"{path.name} is {src.session.track_key} / {src.session.car_key}, not {track} / {car}."
                )
            sources.append((path.name, src.session, src.frames()))
        if not ibt:
            ids = sessions or sessions_for(ws, track, car)[-last:]
            if not ids:
                raise WorkspaceError(
                    f"No recorded sessions on {track} / {car} to backtest against."
                )
            for sid in ids:
                session, frames = session_frames(ws, sid, track, car)
                sources.append((sid, session, frames))
        for name, session, frames in sources:
            runs.append(
                _replay(root, name, session, frames, rules + others, testing, settings)
            )
    finally:
        ws.close()
    report = {
        "track": track,
        "car": car,
        "ref_lap_id": plan.ref_lap_id,
        "sessions": [r["session"] for r in runs],
        "rules": {},
    }
    for rule in rules:
        mine = [run["rules"].get(rule.id) for run in runs]
        totals = {
            k: sum(m["stats"][k] for m in mine if m)
            for k in ("occurrences", "skipped", "matched", "fired", "limited")
        }
        lines = [ln for m in mine if m for ln in m["lines"]]
        totals.update(
            {
                k: sum(1 for ln in lines if ln["status"] == k)
                for k in ("said", "cut", "dropped")
            }
        )
        report["rules"][rule.id] = {
            "fingerprint": rule.fingerprint,
            "laps": sum(run["laps_driven"] for run in runs),
            "laps_counted": sum(run["laps"] for run in runs),
            "totals": totals,
            "laps_fired": len(
                {
                    (m["session"], f["lap_no"])
                    for m in mine
                    if m
                    for f in m["firings"]
                    if f["result"] == "fired"
                }
            ),
            "firings": [
                {"session": m["session"], **f} for m in mine if m for f in m["firings"]
            ],
            "lines": lines,
        }
    return report


def _replay(
    root: Path,
    name: str,
    session: SessionInfo,
    frames: Iterator[Frame],
    rules: list[Rule],
    testing: set[str],
    settings: Settings | None,
) -> dict:
    """One recorded session through a coach with RULES: what each rule in TESTING did."""
    coach = LiveCoach.load(
        root,
        session,
        CapturedVoice(),
        settings,
        rules,
        exclude_session=session.session_id,
    )
    lines: dict[str, list[dict]] = {}

    def on_line(e: Line):
        if e.rule in testing:
            lines.setdefault(e.rule, []).append(
                {
                    "status": e.status,
                    "text": e.text,
                    "at": round(e.at, 2),
                    "lap": coach.state.lap,
                    "note": e.note,
                }
            )

    coach.on(Line, on_line)
    last = None
    for frame in frames:
        coach.push(frame)
        last = frame
    if last is not None:
        coach.finish(last.session_time)
    engine = coach.rules
    out = {
        "session": name,
        "laps": coach.state.lap,
        "laps_driven": coach.state.laps_driven + 1,
        "rules": {},
    }
    for rule_id in testing:
        out["rules"][rule_id] = {
            "session": name,
            "stats": asdict(engine.stats.get(rule_id) or RuleStats()),
            "firings": [f for f in engine.firings if f["rule"] == rule_id],
            "lines": lines.get(rule_id, []),
        }
    return out


def record_backtest(root: Path, rule: Rule, result: dict) -> Rule:
    """Note the backtest on the rule (activation checks it matches the rule as it is)."""
    totals = result["totals"]
    rule.meta["backtest"] = {
        "fingerprint": rule.fingerprint,
        "at": datetime.now(timezone.utc).isoformat(),
        "laps": result["laps"],
        "laps_fired": result["laps_fired"],
        **totals,
    }
    save_rule(root, rule)
    return rule
