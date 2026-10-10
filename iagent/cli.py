"""The `iagent` command: the coach agent's hands, and yours.

Every agent-facing command takes `--json` (or writes CSV for bulk data), writes errors to stderr
and exits non-zero on failure. The workspace comes from `--workspace` or `IAGENT_WORKSPACE`.
"""

import dataclasses
import json
import logging
from pathlib import Path

import click

from iagent import utils
from iagent.analysis import landmarks
from iagent.analysis.compare import compare, summarize, trace
from iagent.analysis.corners import (
    CornerMap,
    carry_names,
    compare_corners,
    consistency,
    corner_metrics,
    load_map,
    save_map,
)
from iagent.laps.pace import DEFAULT_WITHIN, best_times, group_of, representative
from iagent.laps.recorder import record
from iagent.laps.store import LapRecord
from iagent.laps.tracks import update_track_info
from iagent.references import garage61 as g61
from iagent.references import imports as g61_imports
from iagent.telemetry.ibt import IbtSource
from iagent.telemetry.source import TelemetrySource
from iagent.workspace import Workspace, WorkspaceError
from iagent.testing.synthetic import LapKind, SyntheticSource

DEFAULT_WORKSPACE = Path(__file__).resolve().parent.parent / "workspace"


def _emit(obj) -> None:
    click.echo(json.dumps(obj, indent=2))


def _record_dict(r: LapRecord, best: dict) -> dict:
    out = dataclasses.asdict(r)
    out["reasons"] = list(r.reasons)
    b = best.get(group_of(r))
    out["vs_best_pct"] = round((r.lap_time / b - 1) * 100, 2) if b and r.valid and r.lap_time else None
    return out


def _print_laps(records: list[LapRecord], best: dict) -> None:
    width = max([len("lap")] + [len(r.lap_id) for r in records])
    click.echo(f"{'lap':<{width}} {'time':>9} {'vs best':>8} {'off(s)':>7}  {'valid':<5}  reasons")
    for r in records:
        time = f"{r.lap_time:9.3f}" if r.lap_time is not None else f"{'-':>9}"
        b = best.get(group_of(r))
        gap = f"{(r.lap_time / b - 1) * 100:+7.1f}%" if b and r.valid and r.lap_time else f"{'':>8}"
        click.echo(
            f"{r.lap_id:<{width}} {time} {gap} {r.off_track_s:7.1f}  {str(r.valid):<5}  {', '.join(r.reasons)}"
        )


class Ctx(Workspace):
    @property
    def workspace(self) -> Path:
        return self.root


class _Cli(click.Group):
    """Reports workspace errors (unknown lap, empty store, ...) as clean CLI errors."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except WorkspaceError as e:
            raise click.ClickException(str(e)) from e


@click.group(cls=_Cli)
@click.option("--workspace", type=click.Path(file_okay=False, path_type=Path), envvar="IAGENT_WORKSPACE",
              default=DEFAULT_WORKSPACE, show_default=True, help="Workspace directory (env: IAGENT_WORKSPACE).")
@click.option("--debug", is_flag=True, help="Verbose logging (to stderr).")
@click.pass_context
def cli(click_ctx: click.Context, workspace: Path, debug: bool):
    """iRacing coach toolkit: ingest telemetry, inspect and compare laps."""
    utils.setup_logger("iagent", logging.DEBUG if debug else logging.INFO)
    ctx = Ctx(workspace)
    click_ctx.obj = ctx
    click_ctx.call_on_close(ctx.close)


@cli.command()
@click.argument("sources", nargs=-1, required=True)
@click.option("--laps", default=5, show_default=True, help="Synthetic source only: lap count.")
@click.option("--messy", is_flag=True, help="Synthetic source only: include off-track, pit and reset laps.")
@click.option("--seed", default=0, show_default=True, help="Synthetic source only.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def ingest(ctx: Ctx, sources: tuple[str, ...], laps: int, messy: bool, seed: int, as_json: bool):
    """Segment SOURCES (.ibt files, or 'synthetic') into laps and save them to the workspace.

    Every lap is saved, including partial, pit and reset laps (flagged invalid). Re-ingesting a
    file replaces its laps.
    """
    records: list[LapRecord] = []
    for source in sources:
        src: TelemetrySource
        if source == "synthetic":
            kinds = None
            if messy:
                cycle = [LapKind.CLEAN, LapKind.OFF_TRACK, LapKind.PIT_IN, LapKind.OUT_LAP, LapKind.RESET]
                kinds = [cycle[i % len(cycle)] for i in range(laps)]
            src = SyntheticSource(n_laps=laps, kinds=kinds, seed=seed)
        else:
            try:
                src = IbtSource(source)
            except FileNotFoundError as e:
                raise click.ClickException(f"No such file: {e}") from e
        records += record(src, ctx.store(create=True), source_label=Path(source).name)
        update_track_info(ctx.workspace, src.session)
    best = best_times(records)
    if as_json:
        _emit([_record_dict(r, best) for r in records])
    else:
        _print_laps(records, best)


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def workspace(ctx: Ctx, as_json: bool):
    """Where the workspace is: lap store, notes/ and scratch/ live here."""
    root = ctx.workspace.resolve()
    info = {
        "workspace": str(root),
        "has_laps": (root / "index.sqlite").exists(),
        "notes": str(root / "notes"),
        "scratch": str(root / "scratch"),
    }
    if as_json:
        _emit(info)
    else:
        for key, value in info.items():
            click.echo(f"{key}: {value}")


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def tracks(ctx: Ctx, as_json: bool):
    """Track/car combinations in the workspace, with lap counts and best times.

    Use the `track` and `car` keys to filter other commands.
    """
    records = ctx.store().list()
    best = best_times(records)
    rows: dict[tuple[str, str], dict] = {}
    for r in records:
        row = rows.setdefault(group_of(r), {
            "track": r.track_key, "car": r.car_key, "track_name": r.track, "car_name": r.car,
            "laps": 0, "valid_laps": 0, "sessions": set(), "best_lap_time": best.get(group_of(r)),
        })
        row["laps"] += 1
        row["valid_laps"] += int(r.valid)
        row["sessions"].add(r.session_id)
    out = [{**row, "sessions": sorted(row["sessions"])} for row in rows.values()]
    for row in out:
        row["representative_laps"] = len(
            representative([r for r in records if group_of(r) == (row["track"], row["car"])])
        )
    if as_json:
        _emit(out)
        return
    for row in out:
        best_s = f"{row['best_lap_time']:.3f}" if row["best_lap_time"] else "-"
        click.echo(
            f"{row['track']} / {row['car']}  ({row['track_name']}, {row['car_name']}): "
            f"{row['laps']} laps, {row['valid_laps']} valid, {row['representative_laps']} "
            f"representative, best {best_s}, {len(row['sessions'])} session(s)"
        )


@cli.group(invoke_without_command=True)
@click.option("--host", default="127.0.0.1", show_default=True, help="Address to serve on.")
@click.option("--port", default=8765, show_default=True, help="Port to serve on.")
@click.option("--no-browser", is_flag=True, help="Don't open a browser tab.")
@click.option("--telemetry-dir", type=click.Path(file_okay=False, path_type=Path),
              help="iRacing telemetry folder to watch for new recordings (default: the folder last chosen in the "
              "UI, else IAGENT_TELEMETRY_DIR, else Documents/iRacing/telemetry).")
@click.option("--no-watch", is_flag=True, help="Don't watch the telemetry folder.")
@click.pass_context
def ui(click_ctx: click.Context, host: str, port: int, no_browser: bool, telemetry_dir: Path | None, no_watch: bool):
    """Open the lap analysis UI in your browser (runs until Ctrl+C)."""
    if click_ctx.invoked_subcommand is not None:
        return
    ctx: Ctx = click_ctx.obj
    import threading
    import webbrowser

    import uvicorn

    from iagent.laps.watch import TelemetryWatcher, default_telemetry_dir, saved_telemetry_dir
    from iagent.ui.server import LOCAL_HOSTS, STATIC_DIR, create_app

    if not (STATIC_DIR / "index.html").exists():
        click.echo("The UI isn't built yet: run `npm --prefix web install && npm --prefix web run build`.", err=True)
    url = f"http://{host}:{port}/"
    click.echo(f"iRacing Coach UI on {url} (workspace: {ctx.workspace.resolve()}). Ctrl+C to stop.")
    if not no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    watcher = None
    folder = telemetry_dir or saved_telemetry_dir(ctx.workspace) or default_telemetry_dir()
    if not no_watch:
        watcher = TelemetryWatcher(ctx.workspace, folder)
        watcher.start()
        click.echo(f"Watching {folder} for new recordings.")
    # Bound to the network on purpose (e.g. to open it from another machine): accept that Host.
    hosts = ["*"] if host in ("0.0.0.0", "::") else [*LOCAL_HOSTS, host]
    uvicorn.run(create_app(ctx.workspace, watcher=watcher, allowed_hosts=hosts), host=host, port=port,
                log_level="warning")


@ui.command("show")
@click.option("--corner", "corners", type=int, multiple=True, help="Corner number to highlight (repeatable).")
@click.option("--from", "start_m", type=float, help="Zoom the traces from this distance (m).")
@click.option("--to", "end_m", type=float, help="Zoom the traces to this distance (m).")
@click.option("--view", type=click.Choice(["lap", "corner"]),
              help="corner: open the corner view for the first --corner; lap: back to the whole lap.")
@click.option("--lap", "lap_id", help="Switch the review to this lap.")
@click.option("--ref", "ref_id", help="Switch the ghost to this lap.")
@click.pass_obj
def ui_show(ctx: Ctx, corners: tuple[int, ...], start_m: float | None, end_m: float | None, view: str | None,
            lap_id: str | None, ref_id: str | None):
    """Point at something in the open lap review (for the coach in the UI's chat).

    Prints a `ui_action` the UI applies: e.g. `iagent ui show --corner 9 --view corner`, or
    `iagent ui show --from 3600 --to 4200`.
    """
    if (start_m is None) != (end_m is None):
        raise click.UsageError("Give both --from and --to.")
    if start_m is not None and end_m <= start_m:
        raise click.UsageError("--to must be after --from.")
    if any(c < 1 for c in corners):
        raise click.UsageError("Corners are numbered from 1 (T1).")
    lap = ctx.find(lap_id) if lap_id else None
    if ref_id:
        ref = ctx.find(ref_id)
        if lap is not None and group_of(ref) != group_of(lap):
            raise click.UsageError(f"{ref_id} is on a different track or car from {lap_id}.")
    action = {k: v for k, v in {
        "corners": list(corners) or None,
        "range": [start_m, end_m] if start_m is not None else None,
        "view": view,
        "lap": lap_id,
        "ref": ref_id,
    }.items() if v is not None}
    if not action:
        raise click.UsageError("Nothing to show: give --corner, --from/--to, --view, --lap or --ref.")
    _emit({"ui_action": action})


@cli.group()
def laps():
    """List, inspect and compare laps."""


@laps.command("list")
@click.option("--track", help="Track key (see `iagent tracks`).")
@click.option("--car", help="Car key (see `iagent tracks`).")
@click.option("--session", "session_id", help="Session id (the .ibt file stem).")
@click.option("--valid-only", is_flag=True, help="Only structurally valid laps (complete, no pit road, no reset).")
@click.option("--representative", "within", is_flag=False, flag_value=DEFAULT_WITHIN, default=None,
              type=float, help="Only valid laps within FRACTION of the best lap for that track/car "
              "(0.05 if given without a value).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def laps_list(ctx: Ctx, track: str | None, car: str | None, session_id: str | None,
              valid_only: bool, within: float | None, as_json: bool):
    """Laps with time, gap to the best valid lap (same track/car), off-track time and validity."""
    store = ctx.store()
    records = store.list(track=track, car=car, session_id=session_id, valid_only=valid_only,
                         within_best=within)
    best = best_times(store.list(track=track, car=car))
    if as_json:
        _emit([_record_dict(r, best) for r in records])
    else:
        _print_laps(records, best)


@laps.command("show")
@click.argument("lap_id")
@click.option("--sections", default=10, show_default=True, help="Number of equal-distance splits.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def laps_show(ctx: Ctx, lap_id: str, sections: int, as_json: bool):
    """One lap: headline numbers and equal-distance splits (time, min speed, first brake point)."""
    rec = ctx.find(lap_id)
    out = {
        **_record_dict(rec, best_times(ctx.store().list(track=rec.track_key, car=rec.car_key))),
        **summarize(ctx.load(lap_id), rec.lap_time, sections),
    }
    if as_json:
        _emit(out)
        return
    for key, value in out.items():
        if key != "splits":
            click.echo(f"{key}: {value}")
    click.echo(f"{'sec':>3} {'from':>6} {'to':>6} {'time':>8} {'min kph':>8} {'brake@':>8}")
    for s in out["splits"]:
        brake = f"{s['brake_start_m']:.0f}" if s["brake_start_m"] is not None else "-"
        click.echo(f"{s['section']:>3} {s['start_m']:>6} {s['end_m']:>6} {s['time_s']:>8.3f} "
                   f"{s['min_speed_kph']:>8.1f} {brake:>8}")


@laps.command("compare")
@click.argument("lap_id")
@click.argument("ref_id", required=False)
@click.option("--sections", default=20, show_default=True, help="Number of equal-distance sections.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def laps_compare(ctx: Ctx, lap_id: str, ref_id: str | None, sections: int, as_json: bool):
    """Where LAP_ID gains or loses time against REF_ID, section by section.

    REF_ID defaults to the fastest other valid lap on the same track and car. A positive
    `delta_s` means LAP_ID was slower in that section.
    """
    rec = ctx.find(lap_id)
    ref = ctx.reference(rec, ref_id)
    result = compare(ctx.load(lap_id), rec.lap_time, ctx.load(ref.lap_id), ref.lap_time,
                     sections)
    out = {"lap": lap_id, "lap_time": rec.lap_time, "ref": ref.lap_id, "ref_lap_time": ref.lap_time, **result}
    if as_json:
        _emit(out)
        return
    total = f"{out['total_delta_s']:+.3f}s" if out["total_delta_s"] is not None else "n/a"
    click.echo(f"{lap_id} vs {ref.lap_id}: {total}  (biggest losses in sections {out['biggest_losses']})")
    click.echo(f"{'sec':>3} {'from':>6} {'to':>6} {'delta':>7} {'min kph':>8} {'ref':>6} {'brake@':>7} {'ref':>6}")
    for s in out["sections"]:
        def fmt(v, spec):
            return format(v, spec) if v is not None else "-"
        click.echo(f"{s['section']:>3} {s['start_m']:>6} {s['end_m']:>6} {s['delta_s']:>+7.3f} "
                   f"{fmt(s['min_speed_kph'], '8.1f'):>8} {fmt(s['ref_min_speed_kph'], '6.1f'):>6} "
                   f"{fmt(s['brake_start_m'], '7.0f'):>7} {fmt(s['ref_brake_start_m'], '6.0f'):>6}")


@laps.command("trace")
@click.argument("lap_id")
@click.option("--channels", default="Speed,Throttle,Brake,Gear,SteeringWheelAngle", show_default=True,
              help="Comma-separated channel names.")
@click.option("--from", "start_m", default=0.0, show_default=True, help="Start distance (m).")
@click.option("--to", "end_m", type=float, default=None, help="End distance (m); default end of lap.")
@click.option("--step", "step_m", default=10.0, show_default=True, help="Sample spacing (m).")
@click.pass_obj
def laps_trace(ctx: Ctx, lap_id: str, channels: str, start_m: float, end_m: float | None, step_m: float):
    """Channel values along the lap as CSV (LapDist in m, lap_time_s, Speed in km/h).

    Keep ranges short (a corner is ~100-300 m) to avoid flooding the output.
    """
    ctx.find(lap_id)
    names = [c.strip() for c in channels.split(",") if c.strip() not in ("", "LapDist", "lap_time_s")]
    try:
        df = trace(ctx.load(lap_id), names, start_m, end_m, step_m)
    except KeyError as e:
        raise click.ClickException(str(e.args[0])) from e
    click.echo(df.to_csv(index=False), nl=False)

# --- corners --------------------------------------------------------------------------------

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


def _fmt(v, spec: str) -> str:
    return format(v, spec) if v is not None else "-"


def _off(row: dict) -> str:
    """Off-track metres for a compare row, shown only when either lap left the track."""
    a, b = row.get("off_track_m") or 0, row.get("ref_off_track_m") or 0
    return f"{a:.0f}/{b:.0f}" if a or b else ""


@cli.group()
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
        _emit({**dataclasses.asdict(cmap)})
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
        _emit(dataclasses.asdict(cmap))
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
            _emit({"track": track, "source_track": None, "landmarks": []})
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
        _emit({"track": track, "source_track": source_track, "landmarks": matches, "applied": applied})
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
        _emit({"lap": lap_id, "lap_time": rec.lap_time, "track": rec.track_key, "corners": rows})
        return
    click.echo(f"{lap_id}  {rec.lap_time:.3f}s" if rec.lap_time else lap_id)
    click.echo(f"{'corner':<24} {'time':>7} {'brake@':>7} {'entry':>6} {'min':>6} {'@':>6} {'full@':>6} {'exit':>6} {'off m':>5}")
    for r in rows:
        if r.get("missing"):
            continue
        label = f"T{r['corner']} {r['name']}" if r["name"] else f"T{r['corner']}"
        click.echo(f"{label:<24} {_fmt(r['time_s'], '7.3f'):>7} {_fmt(r['brake_m'], '7.0f'):>7} "
                   f"{_fmt(r['entry_speed_kph'], '6.1f'):>6} {_fmt(r['min_speed_kph'], '6.1f'):>6} "
                   f"{_fmt(r['min_speed_m'], '6.0f'):>6} {_fmt(r['full_throttle_m'], '6.0f'):>6} "
                   f"{_fmt(r['exit_speed_kph'], '6.1f'):>6} {_fmt(r['off_track_m'] or None, '5.0f'):>5}")


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
        _emit(out)
        return
    click.echo(f"{lap_id} vs {ref.lap_id}: {_fmt(total, '+.3f')}s  (biggest losses: "
               f"{', '.join(f'T{i}' for i in out['biggest_losses']) or 'none'})")
    click.echo(f"{'corner':<24} {'delta':>7} {'brake':>6} {'min kph':>8} {'full thr':>9} {'exit kph':>9}  off m (lap/ref)")
    for r in rows:
        if r.get("missing"):
            continue
        label = f"T{r['corner']} {r['name']}" if r["name"] else f"T{r['corner']}"
        click.echo(f"{label:<24} {_fmt(r['delta_s'], '+7.3f'):>7} {_fmt(r['brake_diff_m'], '+6.0f'):>6} "
                   f"{_fmt(r['min_speed_diff_kph'], '+8.1f'):>8} {_fmt(r['full_throttle_diff_m'], '+9.0f'):>9} "
                   f"{_fmt(r['exit_speed_diff_kph'], '+9.1f'):>9}  {_off(r)}")


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
        _emit({"track": track, "laps": [r.lap_id for r in reps], "corners": rows})
        return
    click.echo(f"{track}: {len(reps)} representative laps")
    click.echo(f"{'corner':<24} {'time sd':>8} {'brake@ mean/sd':>15} {'min kph mean/sd':>16} {'full@ mean/sd':>14}")
    for r in rows:
        label = f"T{r['corner']} {r['name']}" if r["name"] else f"T{r['corner']}"
        brake = f"{_fmt(r['brake_mean_m'], '.0f')}/{_fmt(r['brake_spread_m'], '.0f')}"
        speed = f"{_fmt(r['min_speed_mean_kph'], '.1f')}/{_fmt(r['min_speed_spread_kph'], '.1f')}"
        thr = f"{_fmt(r['full_throttle_mean_m'], '.0f')}/{_fmt(r['full_throttle_spread_m'], '.0f')}"
        click.echo(f"{label:<24} {_fmt(r['time_spread_s'], '8.3f'):>8} {brake:>15} {speed:>16} {thr:>14}")


# --- reference laps (Garage61) ----------------------------------------------------------------


def _garage61_client() -> g61.Garage61Client:
    return g61_imports.client_from_env()


@cli.group()
def garage61():
    """Find and import reference laps from Garage61 (your laps and your teammates')."""


@garage61.command("status")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def garage61_status(as_json: bool):
    """Check the Garage61 token and show which teams' laps it can reach."""
    client = _garage61_client()
    try:
        me = client.me()
        teams = client.teams()
    except g61.Garage61Error as e:
        raise click.ClickException(str(e)) from e
    finally:
        client.close()
    out = {"user": me.get("slug"), "teams": [{"slug": t.get("slug"), "name": (t.get("name") or "").strip()} for t in teams]}
    if as_json:
        _emit(out)
    else:
        click.echo(f"Token OK for {out['user']}. Teams: {', '.join(t['slug'] for t in out['teams']) or 'none'}")


@garage61.command("find")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--car", help="Car key (default: the only car you've driven there).")
@click.option("--team", "teams", multiple=True, help="Team slug(s) to include (default: all your teams).")
@click.option("--limit", default=20, show_default=True, help="Maximum laps to list.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def garage61_find(ctx: Ctx, track: str, car: str | None, teams: tuple[str, ...], limit: int, as_json: bool):
    """Best Garage61 lap per driver for this track and car: yours and your teammates', fastest first.

    `telemetry` says whether you can import the lap. A personal token only reaches you and your
    Garage61 teammates.
    """
    client = _garage61_client()
    try:
        found = g61_imports.find(ctx, client, track, car, teams, limit)
    except g61.Garage61Error as e:
        raise click.ClickException(str(e)) from e
    finally:
        client.close()
    car, own_best, rows = found["car"], found["your_best"], found["laps"]
    if as_json:
        _emit(found)
        return
    click.echo(f"{track} / {car}: your best {own_best:.3f}s" if own_best else f"{track} / {car}")
    click.echo(f"{'garage61 id':<28} {'driver':<22} {'time':>9} {'vs you':>7} {'rating':>6} {'date':<10} {'track °C':>8} {'telemetry':>9} {'ghost':>5}")
    for r in rows:
        click.echo(f"{r['garage61_id']:<28} {(r['driver'] or '')[:22]:<22} {_fmt(r['lap_time'], '9.3f'):>9} "
                   f"{_fmt(r.get('vs_your_best_pct'), '+6.1f'):>6}% {_fmt(r['driver_rating'], '6.0f'):>6} "
                   f"{r['date']:<10} {_fmt(r['track_temp_c'], '8.1f'):>8} {'yes' if r['can_view_telemetry'] else 'no':>9} {'yes' if r['ghost_available'] else 'no':>5}")


@garage61.command("import")
@click.argument("garage61_ids", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def garage61_import(ctx: Ctx, garage61_ids: tuple[str, ...], as_json: bool):
    """Download Garage61 laps' telemetry and store them as reference laps.

    The lap must be on a track (and car) you have recorded yourself, so it can be compared with
    your laps. Use the printed lap id with `iagent corners compare YOUR_LAP REF_LAP`.
    """
    client = _garage61_client()
    try:
        out = [g61_imports.import_lap(ctx, client, gid) for gid in garage61_ids]
    except g61.Garage61Error as e:
        raise click.ClickException(str(e)) from e
    finally:
        client.close()
    if as_json:
        _emit(out)
        return
    for r in out:
        note = "" if r["same_car_as_yours"] else "  (a car you haven't recorded: not comparable with your laps)"
        click.echo(f"{r['lap_id']}: {r['driver']} {_fmt(r['lap_time'], '.3f')}s on {r['track']} / {r['car']}{note}")


@garage61.command("ghost")
@click.argument("garage61_id")
@click.option("--install", is_flag=True,
              help="Also copy it into iRacing's lapfiles folder (run this on the sim PC).")
@click.option("--lapfiles", type=click.Path(file_okay=False, path_type=Path),
              help="iRacing's lapfiles folder (default: Documents/iRacing/lapfiles, or IAGENT_IRACING_LAPFILES).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def garage61_ghost(ctx: Ctx, garage61_id: str, install: bool, lapfiles: Path | None, as_json: bool):
    """Download a Garage61 lap's ghost (iRacing .blap file) to drive against in the sim.

    Saved under the workspace; with --install also copied to iRacing's lapfiles folder for that
    track. In iRacing: Options > Driving Aids > Load Comparison Lap, and tick "Display Reference
    Car" to see the ghost car.
    """
    client = _garage61_client()
    try:
        out = g61_imports.ghost(ctx, client, garage61_id, install, lapfiles)
    except g61.Garage61Error as e:
        raise click.ClickException(str(e)) from e
    finally:
        client.close()
    if as_json:
        _emit(out)
        return
    click.echo(f"Ghost: {out['driver']} {_fmt(out['lap_time'], '.3f')}s, {out['car_path']} at {out['track_path']}")
    click.echo(f"Saved: {out['saved']}")
    if out["installed"]:
        click.echo(f"Installed for iRacing: {out['installed']}")
    click.echo('In iRacing: Options > Driving Aids > Load Comparison Lap; tick "Display Reference Car".')


@cli.group()
def refs():
    """Reference laps (other drivers' laps imported for comparison)."""


@refs.command("list")
@click.option("--track", help="Track key.")
@click.option("--car", help="Car key.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def refs_list(ctx: Ctx, track: str | None, car: str | None, as_json: bool):
    """Imported reference laps, fastest first, with driver and conditions."""
    store = ctx.refs()
    records = sorted(store.list(track=track, car=car) if store else [], key=lambda r: r.lap_time or float("inf"))
    rows = [{**ctx.ref_meta(r.lap_id), "lap_id": r.lap_id, "track": r.track_key, "car": r.car_key,
             "lap_time": r.lap_time, "valid": r.valid, "off_track_s": r.off_track_s} for r in records]
    if as_json:
        _emit(rows)
        return
    if not rows:
        click.echo("No reference laps. Find some with `iagent garage61 find --track T`.")
    for r in rows:
        click.echo(f"{r['lap_id']:<36} {(r.get('driver') or '')[:22]:<22} {_fmt(r['lap_time'], '9.3f')}  "
                   f"{r['track']} / {r['car']}  {r.get('date', '')}")


@cli.group()
def cues():
    """Spoken corner cues for learning a track (what the live coach says approaching each corner)."""


def _cue_rows(plan) -> list[dict]:
    return [{"corners": c.corners, "target_m": round(c.target_m), "text": c.text, "source": c.source} for c in plan.cues]


def _print_plan(plan) -> None:
    click.echo(f"{plan.track_key} / {plan.car_key}, following {plan.ref_lap_id}")
    for c in plan.cues:
        corners = "+".join(f"T{i}" for i in c.corners)
        click.echo(f"  {corners:<8} {c.target_m:7.0f} m  {c.text}" + ("" if c.source == "template" else f"  [{c.source}]"))


@cues.command("build")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--car", required=True, help="Car key.")
@click.option("--ref", "ref_id", help="Lap to follow (default: fastest Garage61 lap, else your best).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def cues_build(ctx: Ctx, track: str, car: str, ref_id: str | None, as_json: bool):
    """Build (or rebuild) the cue plan from the corner map and a reference lap.

    Each cue must finish at its target: the reference lap's brake point, or just before turn-in
    for corners taken without braking. Text rewritten with `iagent cues set` is kept.
    """
    from iagent.live.cues import build_plan, save_plan

    plan = build_plan(ctx, track, car, ref_id)
    path = save_plan(ctx.workspace, plan)
    if as_json:
        _emit({"path": str(path), "ref_lap_id": plan.ref_lap_id, "cues": _cue_rows(plan)})
    else:
        _print_plan(plan)
        click.echo(f"Saved {path}")


@cues.command("show")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--car", required=True, help="Car key.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def cues_show(ctx: Ctx, track: str, car: str, as_json: bool):
    """The saved cue plan."""
    from iagent.live.cues import load_plan

    plan = load_plan(ctx.workspace, track, car)
    if plan is None:
        raise click.ClickException(f"No cue plan for {track} / {car}: run `iagent cues build --track {track} --car {car}`.")
    if as_json:
        _emit({"ref_lap_id": plan.ref_lap_id, "cues": _cue_rows(plan)})
    else:
        _print_plan(plan)


@cues.command("set")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--car", required=True, help="Car key.")
@click.argument("corner", type=int)
@click.argument("text")
@click.option("--source", default="coach", show_default=True, help="Who wrote it: coach or driver.")
@click.pass_obj
def cues_set(ctx: Ctx, track: str, car: str, corner: int, text: str, source: str):
    """Rewrite the cue for CORNER (the cue covering it). Keep it short: it is spoken on the approach
    and must finish before the brake point; about 14 characters take a second to say."""
    from iagent.live.cues import load_plan, save_plan

    plan = load_plan(ctx.workspace, track, car)
    if plan is None:
        raise click.ClickException(f"No cue plan for {track} / {car}: run `iagent cues build` first.")
    cue = plan.cue_for(corner)
    if cue is None:
        raise click.ClickException(f"No cue covers T{corner}.")
    cue.text, cue.source = text.strip(), source
    save_plan(ctx.workspace, plan)
    click.echo(f"T{'+T'.join(map(str, cue.corners))}: {cue.text}")


@cli.group()
def live():
    """The in-session coach: corner cues and feedback spoken while you drive."""


@live.command("run")
@click.option("--replay", "replay", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Replay an .ibt recording in real time instead of reading iRacing live.")
@click.option("--speed", default=1.0, show_default=True, help="Replay speed (× real time).")
@click.option("--start", "start_at", type=float, help="Replay from this session time (s).")
@click.option("--voice", default="alba", show_default=True, help="Pocket TTS voice, or a .wav to clone.")
@click.option("--print", "print_only", is_flag=True, help="Print what would be said instead of speaking.")
@click.option("--ref", "ref_id", help="Lap to follow (default: the saved cue plan's).")
@click.option("--learning-laps", default=2, show_default=True, help="Laps with every corner cued.")
@click.option("--threads", default=2, show_default=True, help="CPU threads for speech.")
@click.pass_obj
def live_run(ctx: Ctx, replay: Path | None, speed: float, start_at: float | None, voice: str, print_only: bool,
             ref_id: str | None, learning_laps: int, threads: int):
    """Coach live: cue each corner on the approach, say what went wrong after it, sum up each lap.

    On the sim PC this reads iRacing (start it before or after; Ctrl+C to stop). Elsewhere, use
    --replay to hear a recording. Cues come from `iagent cues build` (built on first use).
    """
    from iagent.live.coach import Settings
    from iagent.live.run import run
    from iagent.live.sources import LIVE_CHANNELS, IrsdkSource, paced
    from iagent.live.speech import PrintVoice

    settings = Settings(learning_laps=learning_laps)
    if print_only:
        out = PrintVoice()
    else:
        from iagent.live.voice import PocketVoice

        try:
            click.echo(f"Loading voice {voice}...")
            out = PocketVoice(ctx.workspace / "cache" / "voice", voice=voice, threads=threads)
        except RuntimeError as e:
            raise click.ClickException(str(e)) from e
        out = _SpokenToo(out)

    on_start = lambda coach: click.echo(  # noqa: E731
        f"Coaching {coach.session.track_name} in {coach.session.car_name}: {len(coach.plan.cues)} cues, "
        f"following {coach.plan.ref_lap_id}.")
    try:
        if replay:
            src = IbtSource(replay, channels=LIVE_CHANNELS)
            run(ctx.workspace, lambda: src.session, paced(src.frames(), speed, start_at), out, settings, ref_id, on_start)
        else:
            live_src = IrsdkSource()
            click.echo("Waiting for iRacing...")
            live_src.connect()
            run(ctx.workspace, lambda: live_src.session, live_src.frames(), out, settings, ref_id, on_start)
    except KeyboardInterrupt:
        pass
    finally:
        if hasattr(out, "close"):
            out.close()


class _SpokenToo:
    """Speak and print: the terminal shows what the voice is saying."""

    def __init__(self, voice):
        self._voice = voice
        self._print = __import__("iagent.live.speech", fromlist=["PrintVoice"]).PrintVoice()

    def duration(self, text):
        return self._voice.duration(text)

    def play(self, utterance, now):
        self._print.play(utterance, now)
        self._voice.play(utterance, now)

    def prepare(self, texts):
        return self._voice.prepare(texts)

    def close(self):
        self._voice.close()


if __name__ == "__main__":
    cli()
