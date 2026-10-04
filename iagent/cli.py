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
from iagent.analysis.compare import compare, summarize, trace
from iagent.laps.pace import DEFAULT_WITHIN, best_times, group_of, representative
from iagent.laps.recorder import record
from iagent.laps.store import LapRecord, ParquetLapStore
from iagent.telemetry.ibt import IbtSource
from iagent.telemetry.source import TelemetrySource
from iagent.testing.synthetic import LapKind, SyntheticSource

DEFAULT_WORKSPACE = Path("workspace")


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


class Ctx:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._store: ParquetLapStore | None = None

    def store(self, create: bool = False) -> ParquetLapStore:
        if self._store is None:
            if not create and not (self.workspace / "index.sqlite").exists():
                raise click.ClickException(
                    f"No lap store in {self.workspace}. Run `iagent ingest <file.ibt>` first, or "
                    "set --workspace / IAGENT_WORKSPACE."
                )
            try:
                self._store = ParquetLapStore(self.workspace)
            except RuntimeError as e:
                raise click.ClickException(str(e)) from e
        return self._store

    def close(self) -> None:
        if self._store is not None:
            self._store.close()


def _find(ctx: Ctx, lap_id: str) -> LapRecord:
    matches = [r for r in ctx.store().list() if r.lap_id == lap_id]
    if not matches:
        raise click.ClickException(f"Unknown lap {lap_id!r}. See `iagent laps list`.")
    return matches[0]


@click.group()
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
    rec = _find(ctx, lap_id)
    out = {
        **_record_dict(rec, best_times(ctx.store().list(track=rec.track_key, car=rec.car_key))),
        **summarize(ctx.store().load(lap_id), rec.lap_time, sections),
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
    rec = _find(ctx, lap_id)
    if ref_id is None:
        candidates = [r for r in ctx.store().list(track=rec.track_key, car=rec.car_key, valid_only=True)
                      if r.lap_id != lap_id and r.lap_time is not None]
        if not candidates:
            raise click.ClickException("No other valid lap on this track and car to compare against.")
        ref = min(candidates, key=lambda r: r.lap_time)
    else:
        ref = _find(ctx, ref_id)
        if group_of(ref) != group_of(rec):
            raise click.ClickException(
                f"{ref_id} is {ref.track_key}/{ref.car_key}, {lap_id} is {rec.track_key}/{rec.car_key}: "
                "laps on different tracks or cars can't be compared."
            )
    result = compare(ctx.store().load(lap_id), rec.lap_time, ctx.store().load(ref.lap_id), ref.lap_time,
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
    _find(ctx, lap_id)
    names = [c.strip() for c in channels.split(",") if c.strip() not in ("", "LapDist", "lap_time_s")]
    try:
        df = trace(ctx.store().load(lap_id), names, start_m, end_m, step_m)
    except KeyError as e:
        raise click.ClickException(str(e.args[0])) from e
    click.echo(df.to_csv(index=False), nl=False)


if __name__ == "__main__":
    cli()
