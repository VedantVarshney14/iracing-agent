"""Getting telemetry in, and what the workspace holds: `ingest`, `workspace`, `tracks`."""

from pathlib import Path

import click

from iagent.cli.base import Ctx, emit, lap_dict, print_laps
from iagent.laps.lap import Lap
from iagent.laps.pace import best_times, group_of, representative
from iagent.laps.recorder import record
from iagent.laps.tracks import update_track_info
from iagent.telemetry.ibt import IbtSource
from iagent.telemetry.source import TelemetrySource
from iagent.testing.synthetic import LapKind, SyntheticSource


@click.command()
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
    records: list[Lap] = []
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
        emit([lap_dict(r, best) for r in records])
    else:
        print_laps(records, best)


@click.command()
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
        emit(info)
    else:
        for key, value in info.items():
            click.echo(f"{key}: {value}")


@click.command()
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
        emit(out)
        return
    for row in out:
        best_s = f"{row['best_lap_time']:.3f}" if row["best_lap_time"] else "-"
        click.echo(
            f"{row['track']} / {row['car']}  ({row['track_name']}, {row['car_name']}): "
            f"{row['laps']} laps, {row['valid_laps']} valid, {row['representative_laps']} "
            f"representative, best {best_s}, {len(row['sessions'])} session(s)"
        )
