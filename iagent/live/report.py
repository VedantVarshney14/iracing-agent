"""After a coached session: what happened, read back from its log (`sessions/live/<id>.jsonl`).

The review page shows: the session at a glance, which laps were pushing (and where they weren't),
where the time went, whether each piece of advice worked on the laps after it, and everything
the coach said or held back, lap by lap.
"""

import json
import math
from pathlib import Path

import numpy as np

from iagent.analysis.position import has_position, to_local_xy
from iagent.workspace import Workspace, WorkspaceError

LOSS_S = 0.08  # a corner within this of the reference is matched
IMPROVED_S = 0.08  # advice "worked" when the corner got at least this much better


def sessions_dir(root: Path) -> Path:
    return root / "sessions" / "live"


def read_log(root: Path, session_id: str) -> list[dict]:
    if not session_id.replace("-", "").isalnum():
        raise WorkspaceError(f"Unknown session {session_id!r}.")
    path = sessions_dir(root) / f"{session_id}.jsonl"
    if not path.exists():
        raise WorkspaceError(f"Unknown session {session_id!r}.")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def list_sessions(root: Path) -> list[dict]:
    """Coached sessions with laps, newest first."""
    out = []
    for path in sorted(sessions_dir(root).glob("*.jsonl"), reverse=True):
        try:
            events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError):
            continue
        start = _start(events)
        laps = [e for e in events if e["type"] == "lap"]
        if start is None:
            continue
        pushing = [e["lap_time"] for e in laps if e.get("pace") != "tranquille" and e.get("lap_time")]
        focus = _focus_outcomes(events, laps)
        out.append({
            "id": path.stem,
            "track": start.get("track"), "track_key": start.get("track_key"),
            "car": start.get("car"), "car_key": start.get("car_key"),
            "started": events[0]["wall"],
            "source": start.get("source"),
            "laps": len(laps),
            "best_lap": min(pushing) if pushing else None,
            "ref": start.get("ref"),
            "focus": focus[-1] if focus else None,
        })
    return out


def session_report(ws: Workspace, session_id: str) -> dict:
    events = read_log(ws.root, session_id)
    start = _start(events) or {}
    laps = [e for e in events if e["type"] == "lap"]
    ref_time = start.get("ref_lap_time")
    lines = [e for e in events if e["type"] == "line"]

    times = [e["at"] for e in events if e.get("at") is not None]
    pushing = [e for e in laps if e.get("pace") != "tranquille" and e.get("lap_time")]
    best = min(pushing, key=lambda e: e["lap_time"]) if pushing else None
    held = [e for e in lines if e["status"] == "dropped"]
    reasons: dict[str, int] = {}
    for e in held:
        reasons[e.get("note") or "it expired"] = reasons.get(e.get("note") or "it expired", 0) + 1

    out = {
        "id": session_id,
        "started": events[0]["wall"] if events else None,
        "ended": any(e["type"] == "status" and e.get("state") in ("ended", "stopped") for e in events),
        "track": {"key": start.get("track_key"), "name": start.get("track"), "car": start.get("car_key"),
                  "car_name": start.get("car"), "length_m": start.get("length_m")},
        "ref": {"lap_id": start.get("ref"), "lap_time": ref_time, **_ref_meta(ws, start.get("ref"))},
        "source": start.get("source"),
        "duration_s": (max(times) - min(times)) if times else None,
        "summary": {
            "laps": len(laps),
            "pushing": sum(e.get("pace") == "pushing" for e in laps),
            "moments": sum(e.get("pace") == "moment" for e in laps),
            "tranquille": sum(e.get("pace") == "tranquille" for e in laps),
            "best_lap": best["lap_time"] if best else None,
            "best_lap_no": best["lap"] if best else None,
            "best_gap_s": round(best["lap_time"] - ref_time, 3) if best and ref_time else None,
            "said": sum(e["status"] == "said" for e in lines),
            "cut": sum(e["status"] == "cut" for e in lines),
            "held_back": len(held),
            "held_reasons": reasons,
        },
        "laps": _laps(ws, start, laps, events),
        "corners": _corner_losses(laps),
        "focus": _focus_outcomes(events, laps),
        "advice": _advice_outcomes(events, laps),
        "commentary": _commentary(events, laps),
        "map": _map(ws, start),
        "debrief": read_debrief(ws.root, session_id),
    }
    out["track"]["corners"] = _corner_marks(ws, start.get("track_key"))
    out["context"] = context_lines(out)
    return out


def _corner_marks(ws: Workspace, track: str | None) -> list[dict]:
    if not track:
        return []
    try:
        return [{"id": c.id, "name": c.name, "apex_m": c.apex_m} for c in ws.corner_map(track).corners]
    except WorkspaceError:
        return []


# --- pieces ------------------------------------------------------------------------------------


def _start(events: list[dict]) -> dict | None:
    return next((e for e in events if e["type"] == "status" and e.get("state") == "running"), None)


def _ref_meta(ws: Workspace, lap_id: str | None) -> dict:
    meta = ws.ref_meta(lap_id) if lap_id else {}
    return {"driver": meta.get("driver")}


def _laps(ws: Workspace, start: dict, laps: list[dict], events: list[dict]) -> list[dict]:
    stored = _stored_laps(ws, start)
    out = []
    for lap in laps:
        t0 = lap["at"] - (lap["lap_time"] or 0.0)
        mine = [e for e in events if e["type"] == "line" and e.get("at") is not None and t0 <= e["at"] < lap["at"]]
        out.append({
            "lap": lap["lap"],
            "lap_time": lap["lap_time"],
            "gap_s": lap.get("gap_s"),
            "pace": lap.get("pace", "pushing"),
            "moment_at": lap.get("moment_at"),
            "slow": lap.get("slow") or [],
            "pushing_share": lap.get("pushing_share"),
            "focus": lap.get("focus"),
            "said": sum(e["status"] == "said" for e in mine),
            "held_back": sum(e["status"] == "dropped" for e in mine),
            "lap_id": _match(stored, lap["lap_time"]),
        })
    return out


def _stored_laps(ws: Workspace, start: dict) -> list:
    """The session's laps in the lap store, to link to the lap review (replays, and live sessions
    once iRacing's recording has been ingested)."""
    if not start.get("track_key") or not (ws.root / "index.sqlite").exists():
        return []
    return [r for r in ws.store().list(track=start["track_key"], car=start.get("car_key")) if r.lap_time]


def _match(stored: list, lap_time: float | None) -> str | None:
    if lap_time is None:
        return None
    near = [r for r in stored if abs(r.lap_time - lap_time) < 0.005]
    return near[0].lap_id if len(near) == 1 else None


def _pushing_values(laps: list[dict], corner: int) -> list[tuple[int, float]]:
    """(lap, seconds lost) for a corner, on the laps where it was judged (pushing)."""
    out = []
    for lap in laps:
        for c in lap.get("corners", []):
            if c["corner"] == corner:
                out.append((lap["lap"], c["delta_s"]))
    return out


def _corner_losses(laps: list[dict]) -> list[dict]:
    """Average loss per corner while pushing, for the map."""
    per: dict[int, list[float]] = {}
    for lap in laps:
        for c in lap.get("corners", []):
            per.setdefault(c["corner"], []).append(c["delta_s"])
    return [{"corner": k, "mean_s": round(sum(v) / len(v), 3), "laps": len(v)} for k, v in sorted(per.items())]


def _focus_outcomes(events: list[dict], laps: list[dict]) -> list[dict]:
    focuses = []
    for e in events:
        f = e.get("focus") if e["type"] in ("focus", "status") else None
        if isinstance(f, dict) and not any(x["cue"] == f["cue"] and x["set_lap"] == f["set_lap"] for x in focuses):
            focuses.append(dict(f))
    for f in focuses:
        # The focus cue's loss: the sum over its corners, per pushing lap.
        per_lap = {}
        for lap in laps:
            vals = [c["delta_s"] for c in lap.get("corners", []) if c["corner"] in f["corners"]]
            if len(vals) == len(f["corners"]):
                per_lap[lap["lap"]] = round(sum(vals), 3)
        before = [v for k, v in per_lap.items() if k <= f["set_lap"]]
        after = [(k, v) for k, v in per_lap.items() if k > f["set_lap"]]
        f["before_s"] = before[-1] if before else None
        f["after"] = [{"lap": k, "loss_s": v} for k, v in after]
        last = after[-1][1] if after else None
        f["change_s"] = round(last - f["before_s"], 3) if last is not None and f["before_s"] is not None else None
        f["verdict"] = ("sorted" if f.get("done_lap") and not f.get("replaced") else
                        "replaced" if f.get("replaced") else
                        _verdict(f["before_s"], [v for _, v in after]))
    return focuses


def _advice_outcomes(events: list[dict], laps: list[dict]) -> list[dict]:
    """Each distinct piece of advice (per corner and wording), and that corner on the pushing laps
    after it."""
    said_texts = {e["text"] for e in events if e["type"] == "line" and e["status"] == "said"}
    items: dict[tuple, dict] = {}
    for e in events:
        if e["type"] != "advice":
            continue
        key = (e["corner"], e.get("hint") or e.get("advice"))
        if key in items:
            items[key]["times"] += 1
            continue
        heard = "after the corner" if e.get("advice") in said_texts else None
        if e.get("hint") and any(e["hint"].lower() in t.lower() for t in said_texts):
            heard = "in the next lap's cue" if heard is None else heard
        items[key] = {"corner": e["corner"], "lap": e["lap"], "advice": e.get("advice"), "hint": e.get("hint"),
                      "before_s": e["delta_s"], "heard": heard, "times": 1}
    out = []
    for item in items.values():
        after = [(k, v) for k, v in _pushing_values(laps, item["corner"]) if k > item["lap"]][:3]
        item["after"] = [{"lap": k, "loss_s": v} for k, v in after]
        item["verdict"] = _verdict(item["before_s"], [v for _, v in after])
        out.append(item)
    return sorted(out, key=lambda i: (i["heard"] is None, -i["before_s"]))


def _verdict(before: float | None, after: list[float]) -> str:
    if before is None or not after:
        return "no laps since"
    if all(v <= before - IMPROVED_S or v < LOSS_S for v in after):
        return "working"
    if all(v > before - IMPROVED_S / 2 for v in after):
        return "not yet"
    return "mixed"


def _commentary(events: list[dict], laps: list[dict]) -> list[dict]:
    """Events grouped by lap: "out" before the first counted lap, then each lap, then "after"."""
    bounds = [(lap["lap"], lap["at"] - (lap["lap_time"] or 0.0), lap["at"]) for lap in laps]
    groups: dict[str, list[dict]] = {}
    order: list[str] = []

    def key_for(t: float | None) -> str:
        if t is None or not bounds:
            return "out"
        for n, a, b in bounds:
            if a <= t <= b + 1e-6:  # what happens at the line (the lap's focus, say) belongs to that lap
                return str(n)
        return "out" if t < bounds[0][1] else "after"

    for e in events:
        if e["type"] not in ("line", "driver", "answer", "focus", "pace", "error"):
            continue
        k = key_for(e.get("at"))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(e)
    rank = {"out": -1, "after": 10**6}
    return [{"lap": k, "events": groups[k]} for k in sorted(order, key=lambda k: rank.get(k, int(k) if k.isdigit() else 0))]


def _map(ws: Workspace, start: dict) -> dict | None:
    """The track's shape (from the reference lap's GPS) and where each corner is, in metres."""
    track, ref = start.get("track_key"), start.get("ref")
    if not track or not ref:
        return None
    try:
        grid = ws.load(ref)
        cmap = ws.corner_map(track)
    except (WorkspaceError, KeyError):
        return None
    if not has_position(grid):
        return None
    every = max(1, int(round(8.0 / float(np.median(np.diff(grid["LapDist"]))))))
    g = grid.iloc[::every]
    x, y = to_local_xy(g["Lat"].to_numpy(dtype=float), g["Lon"].to_numpy(dtype=float))
    dist = g["LapDist"].to_numpy(dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    corners = []
    for c in cmap.corners:
        idx = np.nonzero(ok & (dist >= c.segment_start_m) & (dist < c.segment_end_m))[0]
        apex = int(np.argmin(np.abs(dist - c.apex_m)))
        if len(idx):
            corners.append({"id": c.id, "name": c.name, "from": int(idx[0]), "to": int(idx[-1]), "apex": apex})
    return {"x": [round(float(v), 1) if math.isfinite(v) else None for v in x],
            "y": [round(float(v), 1) if math.isfinite(v) else None for v in y],
            "corners": corners}


# --- the coach's debrief -----------------------------------------------------------------------


def debrief_path(root: Path, session_id: str) -> Path:
    return sessions_dir(root) / f"{session_id}.debrief.md"


def read_debrief(root: Path, session_id: str) -> str | None:
    path = debrief_path(root, session_id)
    return path.read_text() if path.exists() else None


def save_debrief(root: Path, session_id: str, text: str) -> None:
    path = debrief_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n")


def context_lines(report: dict) -> list[str]:
    """The session, as the coach is told it when asked to debrief or answer questions."""
    s, ref = report["summary"], report["ref"]
    lines = [f"coached session {report['id']}: {s['laps']} laps ({s['pushing']} pushing, {s['moments']} with a moment, "
             f"{s['tranquille']} tranquille); reference {ref.get('driver') or ref.get('lap_id')} {ref.get('lap_time')}"]
    for lap in report["laps"]:
        lines.append(f"lap {lap['lap']}: {lap['lap_time']:.3f} s, {lap['pace']}"
                     + (f" (moment near T{lap['moment_at']})" if lap["moment_at"] else "")
                     + (f", gap {lap['gap_s']:+.3f}" if lap["gap_s"] is not None and lap["pace"] == "pushing" else "")
                     + (f", lap id {lap['lap_id']}" if lap["lap_id"] else ""))
    lines.append("average loss per corner while pushing: " + ", ".join(f"T{c['corner']} {c['mean_s']:+.2f}" for c in report["corners"]))
    for f in report["focus"]:
        lines.append(f"focus {f['label']} from lap {f['set_lap']}: {f['verdict']}, loss {f['before_s']} -> "
                     + ", ".join(str(a["loss_s"]) for a in f["after"]))
    for a in report["advice"][:8]:
        lines.append(f"advice T{a['corner']} lap {a['lap']} \"{a['hint'] or a['advice']}\": {a['verdict']} "
                     f"({a['before_s']} -> {', '.join(str(x['loss_s']) for x in a['after'])})")
    lines.append(f"coach said {s['said']} lines, held back {s['held_back']}: {s['held_reasons']}")
    return lines
