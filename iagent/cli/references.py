"""Other drivers' laps: `iagent garage61` and `iagent refs`."""

from pathlib import Path

import click

from iagent.cli.base import Ctx, emit, fmt
from iagent.references import garage61 as g61
from iagent.references import imports as g61_imports


def _garage61_client() -> g61.Garage61Client:
    return g61_imports.client_from_env()


@click.group()
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
    out = {
        "user": me.get("slug"),
        "teams": [
            {"slug": t.get("slug"), "name": (t.get("name") or "").strip()}
            for t in teams
        ],
    }
    if as_json:
        emit(out)
    else:
        click.echo(
            f"Token OK for {out['user']}. Teams: {', '.join(t['slug'] for t in out['teams']) or 'none'}"
        )


@garage61.command("find")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--car", help="Car key (default: the only car you've driven there).")
@click.option(
    "--team",
    "teams",
    multiple=True,
    help="Team slug(s) to include (default: all your teams).",
)
@click.option("--limit", default=20, show_default=True, help="Maximum laps to list.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def garage61_find(
    ctx: Ctx,
    track: str,
    car: str | None,
    teams: tuple[str, ...],
    limit: int,
    as_json: bool,
):
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
        emit(found)
        return
    click.echo(
        f"{track} / {car}: your best {own_best:.3f}s"
        if own_best
        else f"{track} / {car}"
    )
    click.echo(
        f"{'garage61 id':<28} {'driver':<22} {'time':>9} {'vs you':>7} {'rating':>6} {'date':<10} {'track °C':>8} {'telemetry':>9} {'ghost':>5}"
    )
    for r in rows:
        click.echo(
            f"{r['garage61_id']:<28} {(r['driver'] or '')[:22]:<22} {fmt(r['lap_time'], '9.3f'):>9} "
            f"{fmt(r.get('vs_your_best_pct'), '+6.1f'):>6}% {fmt(r['driver_rating'], '6.0f'):>6} "
            f"{r['date']:<10} {fmt(r['track_temp_c'], '8.1f'):>8} {'yes' if r['can_view_telemetry'] else 'no':>9} {'yes' if r['ghost_available'] else 'no':>5}"
        )


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
        emit(out)
        return
    for r in out:
        note = (
            ""
            if r["same_car_as_yours"]
            else "  (a car you haven't recorded: not comparable with your laps)"
        )
        click.echo(
            f"{r['lap_id']}: {r['driver']} {fmt(r['lap_time'], '.3f')}s on {r['track']} / {r['car']}{note}"
        )


@garage61.command("ghost")
@click.argument("garage61_id")
@click.option(
    "--install",
    is_flag=True,
    help="Also copy it into iRacing's lapfiles folder (run this on the sim PC).",
)
@click.option(
    "--lapfiles",
    type=click.Path(file_okay=False, path_type=Path),
    help="iRacing's lapfiles folder (default: Documents/iRacing/lapfiles, or IAGENT_IRACING_LAPFILES).",
)
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def garage61_ghost(
    ctx: Ctx, garage61_id: str, install: bool, lapfiles: Path | None, as_json: bool
):
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
        emit(out)
        return
    click.echo(
        f"Ghost: {out['driver']} {fmt(out['lap_time'], '.3f')}s, {out['car_path']} at {out['track_path']}"
    )
    click.echo(f"Saved: {out['saved']}")
    if out["installed"]:
        click.echo(f"Installed for iRacing: {out['installed']}")
    click.echo(
        'In iRacing: Options > Driving Aids > Load Comparison Lap; tick "Display Reference Car".'
    )


@click.group()
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
    records = sorted(
        store.list(track=track, car=car) if store else [],
        key=lambda r: r.lap_time or float("inf"),
    )
    rows = [
        {
            **ctx.ref_meta(r.lap_id),
            "lap_id": r.lap_id,
            "track": r.track_key,
            "car": r.car_key,
            "lap_time": r.lap_time,
            "valid": r.valid,
            "off_track_s": r.off_track_s,
        }
        for r in records
    ]
    if as_json:
        emit(rows)
        return
    if not rows:
        click.echo(
            "No reference laps. Find some with `iagent garage61 find --track T`."
        )
    for r in rows:
        click.echo(
            f"{r['lap_id']:<36} {(r.get('driver') or '')[:22]:<22} {fmt(r['lap_time'], '9.3f')}  "
            f"{r['track']} / {r['car']}  {r.get('date', '')}"
        )
