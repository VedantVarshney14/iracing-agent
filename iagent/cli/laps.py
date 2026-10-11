"""`iagent laps`: list, show, compare and trace laps."""

import click

from iagent.cli.base import Ctx, emit, lap_dict, print_laps
from iagent.analysis.compare import compare, summarize, trace
from iagent.laps.pace import DEFAULT_WITHIN, best_times


@click.group()
def laps():
    """List, inspect and compare laps."""


@laps.command("list")
@click.option("--track", help="Track key (see `iagent tracks`).")
@click.option("--car", help="Car key (see `iagent tracks`).")
@click.option("--session", "session_id", help="Session id (the .ibt file stem).")
@click.option(
    "--valid-only",
    is_flag=True,
    help="Only structurally valid laps (complete, no pit road, no reset).",
)
@click.option(
    "--representative",
    "within",
    is_flag=False,
    flag_value=DEFAULT_WITHIN,
    default=None,
    type=float,
    help="Only valid laps within FRACTION of the best lap for that track/car "
    "(0.05 if given without a value).",
)
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def laps_list(
    ctx: Ctx,
    track: str | None,
    car: str | None,
    session_id: str | None,
    valid_only: bool,
    within: float | None,
    as_json: bool,
):
    """Laps with time, gap to the best valid lap (same track/car), off-track time and validity."""
    store = ctx.store()
    records = store.list(
        track=track,
        car=car,
        session_id=session_id,
        valid_only=valid_only,
        within_best=within,
    )
    best = best_times(store.list(track=track, car=car))
    if as_json:
        emit([lap_dict(r, best) for r in records])
    else:
        print_laps(records, best)


@laps.command("show")
@click.argument("lap_id")
@click.option(
    "--sections", default=10, show_default=True, help="Number of equal-distance splits."
)
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def laps_show(ctx: Ctx, lap_id: str, sections: int, as_json: bool):
    """One lap: headline numbers and equal-distance splits (time, min speed, first brake point)."""
    rec = ctx.find(lap_id)
    out = {
        **lap_dict(
            rec, best_times(ctx.store().list(track=rec.track_key, car=rec.car_key))
        ),
        **summarize(ctx.load(lap_id), rec.lap_time, sections),
    }
    if as_json:
        emit(out)
        return
    for key, value in out.items():
        if key != "splits":
            click.echo(f"{key}: {value}")
    click.echo(
        f"{'sec':>3} {'from':>6} {'to':>6} {'time':>8} {'min kph':>8} {'brake@':>8}"
    )
    for s in out["splits"]:
        brake = f"{s['brake_start_m']:.0f}" if s["brake_start_m"] is not None else "-"
        click.echo(
            f"{s['section']:>3} {s['start_m']:>6} {s['end_m']:>6} {s['time_s']:>8.3f} "
            f"{s['min_speed_kph']:>8.1f} {brake:>8}"
        )


@laps.command("compare")
@click.argument("lap_id")
@click.argument("ref_id", required=False)
@click.option(
    "--sections",
    default=20,
    show_default=True,
    help="Number of equal-distance sections.",
)
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def laps_compare(
    ctx: Ctx, lap_id: str, ref_id: str | None, sections: int, as_json: bool
):
    """Where LAP_ID gains or loses time against REF_ID, section by section.

    REF_ID defaults to the fastest other valid lap on the same track and car. A positive
    `delta_s` means LAP_ID was slower in that section.
    """
    rec = ctx.find(lap_id)
    ref = ctx.reference(rec, ref_id)
    result = compare(
        ctx.load(lap_id), rec.lap_time, ctx.load(ref.lap_id), ref.lap_time, sections
    )
    out = {
        "lap": lap_id,
        "lap_time": rec.lap_time,
        "ref": ref.lap_id,
        "ref_lap_time": ref.lap_time,
        **result,
    }
    if as_json:
        emit(out)
        return
    total = (
        f"{out['total_delta_s']:+.3f}s" if out["total_delta_s"] is not None else "n/a"
    )
    click.echo(
        f"{lap_id} vs {ref.lap_id}: {total}  (biggest losses in sections {out['biggest_losses']})"
    )
    click.echo(
        f"{'sec':>3} {'from':>6} {'to':>6} {'delta':>7} {'min kph':>8} {'ref':>6} {'brake@':>7} {'ref':>6}"
    )
    for s in out["sections"]:

        def fmt(v, spec):
            return format(v, spec) if v is not None else "-"

        click.echo(
            f"{s['section']:>3} {s['start_m']:>6} {s['end_m']:>6} {s['delta_s']:>+7.3f} "
            f"{fmt(s['min_speed_kph'], '8.1f'):>8} {fmt(s['ref_min_speed_kph'], '6.1f'):>6} "
            f"{fmt(s['brake_start_m'], '7.0f'):>7} {fmt(s['ref_brake_start_m'], '6.0f'):>6}"
        )


@laps.command("trace")
@click.argument("lap_id")
@click.option(
    "--channels",
    default="Speed,Throttle,Brake,Gear,SteeringWheelAngle",
    show_default=True,
    help="Comma-separated channel names.",
)
@click.option(
    "--from", "start_m", default=0.0, show_default=True, help="Start distance (m)."
)
@click.option(
    "--to",
    "end_m",
    type=float,
    default=None,
    help="End distance (m); default end of lap.",
)
@click.option(
    "--step", "step_m", default=10.0, show_default=True, help="Sample spacing (m)."
)
@click.pass_obj
def laps_trace(
    ctx: Ctx,
    lap_id: str,
    channels: str,
    start_m: float,
    end_m: float | None,
    step_m: float,
):
    """Channel values along the lap as CSV (LapDist in m, lap_time_s, Speed in km/h).

    Keep ranges short (a corner is ~100-300 m) to avoid flooding the output.
    """
    ctx.find(lap_id)
    names = [
        c.strip()
        for c in channels.split(",")
        if c.strip() not in ("", "LapDist", "lap_time_s")
    ]
    try:
        df = trace(ctx.load(lap_id), names, start_m, end_m, step_m)
    except KeyError as e:
        raise click.ClickException(str(e.args[0])) from e
    click.echo(df.to_csv(index=False), nl=False)
