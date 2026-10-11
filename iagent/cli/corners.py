"""`iagent corners`: the corner map, its names, and per-corner metrics."""

import dataclasses

import click

from iagent.cli.base import Ctx, emit, fmt
from iagent.analysis import landmarks
from iagent.analysis.corners import (
    CornerMap,
    carry_names,
    compare_corners,
    consistency,
    corner_metrics,
    load_map,
    save_map,
)
from iagent.laps.pace import representative


def _corner_dicts(cmap: CornerMap) -> list[dict]:
    return [dataclasses.asdict(c) for c in cmap.corners]


def _print_map(cmap: CornerMap) -> None:
    click.echo(f"{cmap.track_key}: {len(cmap.corners)} corners (from {len(cmap.derived_from)} "
               f"{cmap.car_key} laps)")
    click.echo(f"{'corner':<24} {'dir':>3} {'entry':>6} {'apex':>6} {'exit':>6} {'min kph':>8}  name source")
    for c in cmap.corners:
        flat = " flat" if c.flat else ""
        src = f"{c.name_source}/{c.name_confidence}" if c.name else ""
        click.echo(f"{c.label:<24} {c.direction:>3} {c.entry_m:>6.0f} {c.apex_m:>6.0f} {c.exit_m:>6.0f} "
                   f"{c.ref_min_speed_kph:>8.1f}{flat:<5} {src}")


def _off(row: dict) -> str:
    """Off-track metres for a compare row, shown only when either lap left the track."""
    a, b = row.get("off_track_m") or 0, row.get("ref_off_track_m") or 0
    return f"{a:.0f}/{b:.0f}" if a or b else ""


@click.group()
def corners():
    """Corner map (derived from laps) and per-corner analysis."""


@corners.command("map")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--car", help="Car whose laps to derive from (default: the one with most representative laps).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def corners_map(ctx: Ctx, track: str, car: str | None, as_json: bool):
    """(Re)derive the corner map for a track from its representative laps and save it.

    Corners are stretches of sustained lateral g; chicanes split into one corner per direction.
    Names from an existing map are kept for corners that still line up.
    """
    cmap = ctx.derive_map(track, car)
    old = load_map(ctx.workspace, track)
    if old is not None:
        cmap = carry_names(old, cmap)
    save_map(ctx.workspace, cmap)
    if as_json:
        emit({**dataclasses.asdict(cmap)})
    else:
        _print_map(cmap)


@corners.command("list")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def corners_list(ctx: Ctx, track: str, as_json: bool):
    """The corner map: direction, entry/apex/exit distances (m), reference minimum speed, names."""
    cmap = ctx.corner_map(track)
    if as_json:
        emit(dataclasses.asdict(cmap))
    else:
        _print_map(cmap)


@corners.command("name")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.argument("corner_id", type=int)
@click.argument("name", required=False)
@click.option("--source", default="driver", show_default=True,
              help="Where the name came from, e.g. driver, crewchief, web, model.")
@click.option("--confidence", type=click.Choice(["high", "medium", "low"]), default="high", show_default=True)
@click.option("--clear", is_flag=True, help="Remove the corner's name.")
@click.pass_obj
def corners_name(ctx: Ctx, track: str, corner_id: int, name: str | None, source: str, confidence: str, clear: bool):
    """Name corner CORNER_ID (e.g. `iagent corners name --track spa-2024-up 1 "La Source"`)."""
    if not clear and not name:
        raise click.UsageError("Give a NAME, or --clear.")
    cmap = ctx.corner_map(track)
    try:
        c = cmap.get(corner_id)
    except KeyError as e:
        raise click.ClickException(str(e.args[0])) from e
    c.name, c.name_source, c.name_confidence = (None, None, None) if clear else (name, source, confidence)
    save_map(ctx.workspace, cmap)
    click.echo(f"T{c.id}: {c.name or '(no name)'}")


@corners.command("landmarks")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--apply", is_flag=True,
              help="Name unnamed corners that match exactly one named landmark (source crewchief, medium confidence).")
@click.option("--refresh", is_flag=True, help="Re-download the landmarks file.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def corners_landmarks(ctx: Ctx, track: str, apply: bool, refresh: bool, as_json: bool):
    """Corner-name hints from CrewChief's landmark data, matched to this track's corners.

    Coverage is partial (~25 iRacing tracks) and names can be misspelled or generic ("turn9");
    treat them as hints. Downloads the data once into the workspace.
    """
    cmap = ctx.corner_map(track)
    try:
        data = landmarks.load(ctx.workspace / "cache" / "crewchief-landmarks.json", refresh)
    except OSError as e:
        raise click.ClickException(f"Couldn't fetch CrewChief landmarks: {e}") from e
    found = landmarks.find(data, track)
    if found is None:
        if as_json:
            emit({"track": track, "source_track": None, "landmarks": []})
        else:
            click.echo(f"CrewChief has no landmarks for {track}.")
        return
    source_track, marks = found
    matches = landmarks.match(cmap, marks)
    applied = []
    if apply:
        for m in matches:
            if m["suggested_name"] and len(m["corners"]) == 1:
                c = cmap.get(m["corners"][0])
                if c.name is None:
                    c.name, c.name_source, c.name_confidence = m["suggested_name"], "crewchief", "medium"
                    applied.append(c.id)
        save_map(ctx.workspace, cmap)
    if as_json:
        emit({"track": track, "source_track": source_track, "landmarks": matches, "applied": applied})
        return
    click.echo(f"CrewChief landmarks for {source_track!r} (may be an older scan of {track}):")
    for m in matches:
        corners_s = ", ".join(f"T{i}" for i in m["corners"]) or "no corner"
        click.echo(f"  {m['landmark']:<16} {m['start_m']:>6.0f}-{m['end_m']:<6.0f} -> {corners_s}")
    if apply:
        click.echo(f"Named: {', '.join(f'T{i}' for i in applied) or 'nothing new'}")


@corners.command("report")
@click.argument("lap_id")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def corners_report(ctx: Ctx, lap_id: str, as_json: bool):
    """Per-corner numbers for one lap: time, brake point, entry/min/exit speed, full throttle point.

    Distances are metres from the start/finish line; speeds km/h. A missing brake point means the
    corner was taken without braking (min_throttle shows any lift). `off m` is metres driven off
    the track in that corner: the corner's numbers then describe an incident.
    """
    rec = ctx.find(lap_id)
    cmap = ctx.corner_map(rec.track_key)
    rows = corner_metrics(ctx.load(lap_id), rec.lap_time, cmap)
    if as_json:
        emit({"lap": lap_id, "lap_time": rec.lap_time, "track": rec.track_key, "corners": rows})
        return
    click.echo(f"{lap_id}  {rec.lap_time:.3f}s" if rec.lap_time else lap_id)
    click.echo(f"{'corner':<24} {'time':>7} {'brake@':>7} {'entry':>6} {'min':>6} {'@':>6} {'full@':>6} {'exit':>6} {'off m':>5}")
    for r in rows:
        if r.get("missing"):
            continue
        label = f"T{r['corner']} {r['name']}" if r["name"] else f"T{r['corner']}"
        click.echo(f"{label:<24} {fmt(r['time_s'], '7.3f'):>7} {fmt(r['brake_m'], '7.0f'):>7} "
                   f"{fmt(r['entry_speed_kph'], '6.1f'):>6} {fmt(r['min_speed_kph'], '6.1f'):>6} "
                   f"{fmt(r['min_speed_m'], '6.0f'):>6} {fmt(r['full_throttle_m'], '6.0f'):>6} "
                   f"{fmt(r['exit_speed_kph'], '6.1f'):>6} {fmt(r['off_track_m'] or None, '5.0f'):>5}")


@corners.command("compare")
@click.argument("lap_id")
@click.argument("ref_id", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def corners_compare(ctx: Ctx, lap_id: str, ref_id: str | None, as_json: bool):
    """Corner by corner: where LAP_ID gains or loses time against REF_ID, and why.

    REF_ID defaults to the fastest other valid lap on the same track and car. Signs: delta_s > 0
    slower; brake_diff_m > 0 braked later; min_speed_diff_kph > 0 more speed; full_throttle_diff_m
    > 0 full throttle later; exit_speed_diff_kph > 0 faster exit. Off-track metres are shown when
    either lap left the track in a corner: that corner's difference is then an incident, not technique.
    """
    rec = ctx.find(lap_id)
    ref = ctx.reference(rec, ref_id)
    cmap = ctx.corner_map(rec.track_key)
    rows = compare_corners(
        corner_metrics(ctx.load(lap_id), rec.lap_time, cmap),
        corner_metrics(ctx.load(ref.lap_id), ref.lap_time, cmap),
    )
    total = round(rec.lap_time - ref.lap_time, 3) if rec.lap_time and ref.lap_time else None
    losses = sorted((r for r in rows if (r.get("delta_s") or 0) > 0), key=lambda r: -r["delta_s"])
    out = {"lap": lap_id, "ref": ref.lap_id, "total_delta_s": total,
           "biggest_losses": [r["corner"] for r in losses[:3]], "corners": rows}
    if as_json:
        emit(out)
        return
    click.echo(f"{lap_id} vs {ref.lap_id}: {fmt(total, '+.3f')}s  (biggest losses: "
               f"{', '.join(f'T{i}' for i in out['biggest_losses']) or 'none'})")
    click.echo(f"{'corner':<24} {'delta':>7} {'brake':>6} {'min kph':>8} {'full thr':>9} {'exit kph':>9}  off m (lap/ref)")
    for r in rows:
        if r.get("missing"):
            continue
        label = f"T{r['corner']} {r['name']}" if r["name"] else f"T{r['corner']}"
        click.echo(f"{label:<24} {fmt(r['delta_s'], '+7.3f'):>7} {fmt(r['brake_diff_m'], '+6.0f'):>6} "
                   f"{fmt(r['min_speed_diff_kph'], '+8.1f'):>8} {fmt(r['full_throttle_diff_m'], '+9.0f'):>9} "
                   f"{fmt(r['exit_speed_diff_kph'], '+9.1f'):>9}  {_off(r)}")


@corners.command("consistency")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--car", help="Car key (default: all cars on this track).")
@click.option("--session", "session_id", help="Only this session's laps.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def corners_consistency(ctx: Ctx, track: str, car: str | None, session_id: str | None, as_json: bool):
    """How repeatable each corner is across representative laps: spread (std dev) of corner time,
    brake point, minimum speed and full-throttle point. Lower spread = more consistent."""
    cmap = ctx.corner_map(track)
    reps = [r for r in representative(ctx.store().list(track=track, car=car))
            if session_id is None or r.session_id == session_id]
    if len(reps) < 2:
        raise click.ClickException(f"Need at least two representative laps; found {len(reps)}.")
    rows = consistency([corner_metrics(ctx.store().load(r.lap_id), r.lap_time, cmap) for r in reps])
    if as_json:
        emit({"track": track, "laps": [r.lap_id for r in reps], "corners": rows})
        return
    click.echo(f"{track}: {len(reps)} representative laps")
    click.echo(f"{'corner':<24} {'time sd':>8} {'brake@ mean/sd':>15} {'min kph mean/sd':>16} {'full@ mean/sd':>14}")
    for r in rows:
        label = f"T{r['corner']} {r['name']}" if r["name"] else f"T{r['corner']}"
        brake = f"{fmt(r['brake_mean_m'], '.0f')}/{fmt(r['brake_spread_m'], '.0f')}"
        speed = f"{fmt(r['min_speed_mean_kph'], '.1f')}/{fmt(r['min_speed_spread_kph'], '.1f')}"
        thr = f"{fmt(r['full_throttle_mean_m'], '.0f')}/{fmt(r['full_throttle_spread_m'], '.0f')}"
        click.echo(f"{label:<24} {fmt(r['time_spread_s'], '8.3f'):>8} {brake:>15} {speed:>16} {thr:>14}")
