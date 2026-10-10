"""Where rules live, and backtesting them before they go live.

Each rule is a JSON file: `rules/<track_key>/<id>.json`, or `rules/_any/<id>.json` for a rule on
every track. Its `status` says whether the live coach uses it: `draft` (just added or edited),
`active`, or `archived`. Editing a rule makes it a draft again, and activating needs a backtest
of the rule as it is now, so nothing goes live that hasn't been seen against real laps.

A backtest replays recorded sessions through the *same* live coach and rule engine (with a
captured voice instead of a speaker): where each rule would have fired, what it would have said,
and whether the line would actually have been said or was dropped (and why).
"""

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

from iagent.laps.tracks import load_track_info
from iagent.live.coach import LiveCoach, Settings
from iagent.live.cues import build_plan, load_plan
from iagent.live.rules import Rule, RuleError, RuleStats, check_against_map, uses_corners
from iagent.live.speech import Arbiter, CapturedVoice
from iagent.telemetry.frames import Frame
from iagent.telemetry.session import SessionInfo
from iagent.workspace import Workspace, WorkspaceError

ANY_TRACK = "_any"


def rules_root(root: Path) -> Path:
    return root / "rules"


def rule_path(root: Path, rule: Rule) -> Path:
    return rules_root(root) / (rule.track or ANY_TRACK) / f"{rule.id}.json"


def _files(root: Path) -> Iterator[Path]:
    base = rules_root(root)
    if base.is_dir():
        yield from sorted(base.glob("*/*.json"))


def load_rules(root: Path, track: str | None = None, car: str | None = None,
               status: str | tuple[str, ...] | None = None) -> list[Rule]:
    """Rules for TRACK (and those for every track), optionally only with STATUS. Files that
    don't parse are skipped (`broken_rules` lists them)."""
    statuses = (status,) if isinstance(status, str) else status
    out = []
    for path in _files(root):
        if track is not None and path.parent.name not in (track, ANY_TRACK):
            continue
        try:
            rule = Rule.from_dict(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue
        if statuses and rule.status not in statuses:
            continue
        if car is not None and rule.car not in (None, car):
            continue
        out.append(rule)
    return out


def broken_rules(root: Path) -> list[tuple[Path, str]]:
    out = []
    for path in _files(root):
        try:
            Rule.from_dict(json.loads(path.read_text()))
        except (ValueError, OSError) as e:
            out.append((path, str(e)))
    return out


def find_rule(root: Path, rule_id: str) -> Rule:
    for path in _files(root):
        if path.stem == rule_id:
            try:
                return Rule.from_dict(json.loads(path.read_text()))
            except ValueError as e:
                raise RuleError(f"{path} doesn't parse: {e}") from None
    raise RuleError(f"No rule {rule_id!r}. See `iagent rules list`.")


def save_rule(root: Path, rule: Rule) -> Path:
    path = rule_path(root, rule)
    for other in _files(root):  # a rule moved to another track (or to every track)
        if other.stem == rule.id and other != path:
            other.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rule.to_dict(), indent=2) + "\n")
    return path


def add_rule(root: Path, raw: dict, replace: bool = False, by: str = "coach") -> tuple[Rule, Path]:
    """Validate and save a rule as a draft. Corners are checked against the track's map."""
    raw = {k: v for k, v in raw.items() if k not in ("status", "meta")}
    rule = Rule.from_dict(raw)
    try:
        existing = find_rule(root, rule.id)
    except RuleError:
        existing = None
    if existing is not None and not replace:
        raise RuleError(f"A rule {rule.id!r} exists already: pass --replace to change it, or pick another id.")
    if rule.track is None and uses_corners(rule):
        raise RuleError(f"{rule.id}: a rule about a corner needs a track.")
    if rule.track is not None and uses_corners(rule):
        ws = Workspace(root)
        try:
            cmap = ws.corner_map(rule.track)
        except WorkspaceError as e:
            raise RuleError(f"{rule.id}: can't check its corners: {e}") from None
        finally:
            ws.close()
        check_against_map(rule, cmap)
    now = datetime.now(timezone.utc).isoformat()
    rule.meta = {"created_by": (existing.meta.get("created_by") if existing else None) or by,
                 "created_at": (existing.meta.get("created_at") if existing else None) or now}
    if existing is not None:
        rule.meta["edited_at"] = now
    rule.status = "draft"
    return rule, save_rule(root, rule)


def set_status(root: Path, rule_id: str, status: str, force: bool = False) -> Rule:
    rule = find_rule(root, rule_id)
    if status == "active" and not force:
        bt = rule.meta.get("backtest")
        if not bt or bt.get("fingerprint") != rule.fingerprint:
            raise RuleError(f"Backtest {rule_id} first (`iagent rules backtest {rule_id}`): rules go live only once "
                            "they've been seen against real laps. (--force skips this.)")
    rule.status = status
    rule.meta[f"{status}_at"] = datetime.now(timezone.utc).isoformat()
    save_rule(root, rule)
    return rule


def remove_rule(root: Path, rule_id: str) -> Path:
    rule = find_rule(root, rule_id)
    path = rule_path(root, rule)
    path.unlink()
    return path


def signature(root: Path) -> tuple:
    """Changes when any rule file changes (the live coach reloads its rules then)."""
    out = []
    for path in _files(root):
        try:
            out.append((str(path), path.stat().st_mtime_ns))
        except OSError:
            pass
    return tuple(out)


# --- backtest -----------------------------------------------------------------------------------

def sessions_for(ws: Workspace, track: str, car: str) -> list[str]:
    """The driver's recorded sessions on this track and car, oldest first."""
    if not (ws.root / "index.sqlite").exists():
        return []
    seen: dict[str, None] = {}
    for r in ws.store().list(track=track, car=car):
        seen.setdefault(r.session_id, None)
    return list(seen)


def session_frames(ws: Workspace, session_id: str, track: str, car: str) -> tuple[SessionInfo, Iterator[Frame]]:
    """A stored session's raw samples, in order, as live frames."""
    laps = sorted(ws.store().list(track=track, car=car, session_id=session_id), key=lambda r: r.seq)
    if not laps:
        raise WorkspaceError(f"No laps of session {session_id!r} on {track} / {car}.")
    info = load_track_info(ws.root, track) or {}
    length = info.get("length_m") or ws.corner_map(track).length_m
    session = SessionInfo(
        track_name=info.get("name") or laps[0].track, track_length_m=float(length),
        car_name=(info.get("cars", {}).get(car) or {}).get("name") or laps[0].car,
        track_id=info.get("track_id"), session_id=session_id, track_code=track,
        track_config=info.get("config"), car_path=car,
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


def backtest(root: Path, rules: list[Rule], track: str, car: str, sessions: list[str] | None = None,
             ibt: list[Path] | None = None, last: int = 3, with_active: bool = True,
             settings: Settings | None = None) -> dict:
    """Replay recordings through the live coach with RULES (plus the other active rules, which
    compete for the same voice) and report what each of RULES would have done."""
    testing = {r.id for r in rules}
    others = [r for r in load_rules(root, track, car, "active") if r.id not in testing] if with_active else []
    ws = Workspace(root)
    runs = []
    try:
        cmap = ws.corner_map(track)
        for rule in rules:
            check_against_map(rule, cmap)
        plan = load_plan(root, track, car) or build_plan(ws, track, car)
        ref_grid = ws.load(plan.ref_lap_id)
        sources: list[tuple[str, SessionInfo, Iterator[Frame]]] = []
        for path in ibt or []:
            from iagent.live.sources import LIVE_CHANNELS
            from iagent.telemetry.ibt import IbtSource

            src = IbtSource(path, channels=LIVE_CHANNELS)
            if (src.session.track_key, src.session.car_key) != (track, car):
                raise WorkspaceError(f"{path.name} is {src.session.track_key} / {src.session.car_key}, not {track} / {car}.")
            sources.append((path.name, src.session, src.frames()))
        if not ibt:
            ids = sessions or sessions_for(ws, track, car)[-last:]
            if not ids:
                raise WorkspaceError(f"No recorded sessions on {track} / {car} to backtest against.")
            for sid in ids:
                session, frames = session_frames(ws, sid, track, car)
                sources.append((sid, session, frames))
        for name, session, frames in sources:
            runs.append(_replay(ws, name, session, frames, plan, cmap, ref_grid, rules + others, testing, settings))
    finally:
        ws.close()
    report = {"track": track, "car": car, "ref_lap_id": plan.ref_lap_id, "sessions": [r["session"] for r in runs],
              "rules": {}}
    for rule in rules:
        mine = [run["rules"].get(rule.id) for run in runs]
        totals = {k: sum(m["stats"][k] for m in mine if m) for k in ("occurrences", "skipped", "matched", "fired", "limited")}
        lines = [ln for m in mine if m for ln in m["lines"]]
        totals.update({k: sum(1 for ln in lines if ln["status"] == k) for k in ("said", "cut", "dropped")})
        report["rules"][rule.id] = {
            "fingerprint": rule.fingerprint, "laps": sum(run["laps_driven"] for run in runs),
            "laps_counted": sum(run["laps"] for run in runs), "totals": totals,
            "laps_fired": len({(m["session"], f["lap_no"]) for m in mine if m for f in m["firings"] if f["result"] == "fired"}),
            "firings": [{"session": m["session"], **f} for m in mine if m for f in m["firings"]],
            "lines": lines,
        }
    return report


def _replay(ws: Workspace, name: str, session: SessionInfo, frames: Iterator[Frame], plan, cmap, ref_grid,
            rules: list[Rule], testing: set[str], settings: Settings | None) -> dict:
    voice = CapturedVoice()
    own = _own_best(ws, plan.track_key, plan.car_key, exclude=session.session_id)
    coach = LiveCoach(session, plan, cmap, ref_grid, Arbiter(voice), settings, own_best=own, rules=rules)
    lines: dict[str, list[dict]] = {}

    def on_line(e):
        if e["rule"] in testing:
            lines.setdefault(e["rule"], []).append({"status": e["status"], "text": e["text"], "at": round(e.at, 2),
                                                    "lap": coach.laps, "note": e["note"]})
    coach.on("line", on_line)
    last = None
    for frame in frames:
        coach.push(frame)
        last = frame
    if last is not None:
        coach.finish(last.session_time)
    engine = coach.rules
    out = {"session": name, "laps": coach.laps, "laps_driven": coach.state["laps_driven"] + 1, "rules": {}}
    for rule_id in testing:
        out["rules"][rule_id] = {
            "session": name,
            "stats": asdict(engine.stats.get(rule_id) or RuleStats()),
            "firings": [f for f in engine.firings if f["rule"] == rule_id],
            "lines": lines.get(rule_id, []),
        }
    return out



def _own_best(ws: Workspace, track: str, car: str, exclude: str | None):
    if not (ws.root / "index.sqlite").exists():
        return None
    laps = [r for r in ws.store().list(track=track, car=car, valid_only=True)
            if r.lap_time and r.session_id != exclude]
    return ws.load(min(laps, key=lambda r: r.lap_time).lap_id) if laps else None


def record_backtest(root: Path, rule: Rule, result: dict) -> Rule:
    """Note the backtest on the rule (activation checks it matches the rule as it is)."""
    totals = result["totals"]
    rule.meta["backtest"] = {"fingerprint": rule.fingerprint, "at": datetime.now(timezone.utc).isoformat(),
                             "laps": result["laps"], "laps_fired": result["laps_fired"], **totals}
    save_rule(root, rule)
    return rule
