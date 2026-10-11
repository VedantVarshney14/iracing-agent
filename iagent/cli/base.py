"""What every command group shares: the workspace context and output helpers."""

import dataclasses
import json
from pathlib import Path

import click

from iagent.laps.lap import Lap
from iagent.laps.pace import group_of
from iagent.workspace import Workspace

DEFAULT_WORKSPACE = Path(__file__).resolve().parents[2] / "workspace"


class Ctx(Workspace):
    """The workspace, as every command gets it (`ctx.workspace` is its folder)."""

    @property
    def workspace(self) -> Path:
        return self.root


def emit(obj) -> None:
    click.echo(json.dumps(obj, indent=2))


def fmt(v, spec: str) -> str:
    return format(v, spec) if v is not None else "-"


def lap_dict(lap: Lap, best: dict) -> dict:
    """A lap as JSON for `--json` output, with how far it is off the best valid lap of its track and
    car."""
    out = {f.name: getattr(lap, f.name) for f in dataclasses.fields(lap) if f.name != "frames"}
    out["reasons"] = list(lap.reasons)
    out["complete"], out["valid"] = lap.complete, lap.valid
    b = best.get(group_of(lap))
    out["vs_best_pct"] = round((lap.lap_time / b - 1) * 100, 2) if b and lap.valid and lap.lap_time else None
    return out


def print_laps(laps: list[Lap], best: dict) -> None:
    """Laps as a table: time, gap to the best, time off track, validity and why not."""
    width = max([len("lap")] + [len(r.lap_id) for r in laps])
    click.echo(f"{'lap':<{width}} {'time':>9} {'vs best':>8} {'off(s)':>7}  {'valid':<5}  reasons")
    for r in laps:
        time = f"{r.lap_time:9.3f}" if r.lap_time is not None else f"{'-':>9}"
        b = best.get(group_of(r))
        gap = f"{(r.lap_time / b - 1) * 100:+7.1f}%" if b and r.valid and r.lap_time else f"{'':>8}"
        click.echo(
            f"{r.lap_id:<{width}} {time} {gap} {r.off_track_s:7.1f}  {str(r.valid):<5}  {', '.join(r.reasons)}"
        )
