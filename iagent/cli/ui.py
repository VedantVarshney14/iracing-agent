"""`iagent ui`: the browser app, and `ui show` to point it at something."""

from pathlib import Path

import click

from iagent.cli.base import Ctx, emit
from iagent.laps.pace import group_of


@click.group(invoke_without_command=True)
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
    emit({"ui_action": action})
