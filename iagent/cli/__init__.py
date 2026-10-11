"""The `iagent` command: the coach agent's hands, and yours.

Every agent-facing command takes `--json` (or writes CSV for bulk data), writes errors to stderr
and exits non-zero on failure. The workspace comes from `--workspace` or `IAGENT_WORKSPACE`.
Each command group lives in its own module here.
"""

import logging
from pathlib import Path

import click

from iagent import utils
from iagent.cli import corners, cues, laps, live, references, rules, ui, workspace
from iagent.cli.base import DEFAULT_WORKSPACE, Ctx
from iagent.live.rules import RuleError
from iagent.workspace import WorkspaceError


class _Cli(click.Group):
    """Reports workspace and rule errors (unknown lap, empty store, a bad rule, ...) as clean CLI
    errors."""

    def invoke(self, ctx: click.Context):
        """Run the command, turning workspace and rule errors into clean CLI errors."""
        try:
            return super().invoke(ctx)
        except (WorkspaceError, RuleError) as e:
            raise click.ClickException(str(e)) from e


@click.group(cls=_Cli)
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, path_type=Path),
    envvar="IAGENT_WORKSPACE",
    default=DEFAULT_WORKSPACE,
    show_default=True,
    help="Workspace directory (env: IAGENT_WORKSPACE).",
)
@click.option("--debug", is_flag=True, help="Verbose logging (to stderr).")
@click.pass_context
def cli(click_ctx: click.Context, workspace: Path, debug: bool):
    """iRacing coach toolkit: ingest telemetry, inspect and compare laps."""
    utils.setup_logger("iagent", logging.DEBUG if debug else logging.INFO)
    ctx = Ctx(workspace)
    click_ctx.obj = ctx
    click_ctx.call_on_close(ctx.close)


for command in (
    workspace.ingest,
    workspace.workspace,
    workspace.tracks,
    ui.ui,
    laps.laps,
    corners.corners,
    references.garage61,
    references.refs,
    cues.cues,
    rules.rules,
    live.live,
):
    cli.add_command(command)
