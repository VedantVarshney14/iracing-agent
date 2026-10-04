import logging
from pathlib import Path

import click

from iagent import utils
from iagent.brain.pace import DEFAULT_WITHIN, best_time
from iagent.brain.recorder import record
from iagent.brain.store import LapRecord, ParquetLapStore
from iagent.edge.ibt import IbtSource
from iagent.edge.source import TelemetrySource
from iagent.testing.synthetic import LapKind, SyntheticSource

DEFAULT_STORE = Path("workspace")


def _print_laps(records: list[LapRecord]) -> None:
    best = best_time(records)
    width = max([len("lap")] + [len(r.lap_id) for r in records])
    click.echo(f"{'lap':<{width}} {'time':>9} {'vs best':>8} {'off(s)':>7}  {'valid':<5}  reasons")
    for r in records:
        time = f"{r.lap_time:9.3f}" if r.lap_time is not None else f"{'-':>9}"
        gap = f"{(r.lap_time / best - 1) * 100:+7.1f}%" if best and r.valid and r.lap_time else f"{'':>8}"
        click.echo(
            f"{r.lap_id:<{width}} {time} {gap} {r.off_track_s:7.1f}  {str(r.valid):<5}  {', '.join(r.reasons)}"
        )


@click.group()
@click.option("--debug", is_flag=True, help="Verbose logging.")
def cli(debug: bool):
    utils.setup_logger("iagent", logging.DEBUG if debug else logging.INFO)


@cli.command()
@click.argument("source")
@click.option("--store", "store_dir", type=click.Path(path_type=Path), default=DEFAULT_STORE,
              show_default=True, help="Workspace directory to save laps into.")
@click.option("--laps", default=5, show_default=True, help="Synthetic source only: lap count.")
@click.option("--messy", is_flag=True, help="Synthetic source only: include off-track, pit and reset laps.")
@click.option("--seed", default=0, show_default=True, help="Synthetic source only.")
def replay(source: str, store_dir: Path, laps: int, messy: bool, seed: int):
    """Replay SOURCE (an .ibt file, or 'synthetic') through segmentation into the lap store."""
    src: TelemetrySource
    if source == "synthetic":
        kinds = None
        if messy:
            cycle = [LapKind.CLEAN, LapKind.OFF_TRACK, LapKind.PIT_IN, LapKind.OUT_LAP, LapKind.RESET]
            kinds = [cycle[i % len(cycle)] for i in range(laps)]
        src = SyntheticSource(n_laps=laps, kinds=kinds, seed=seed)
    else:
        src = IbtSource(source)

    store = ParquetLapStore(store_dir)
    try:
        _print_laps(record(src, store, source_label=source))
    finally:
        store.close()


@cli.command("laps")
@click.option("--store", "store_dir", type=click.Path(exists=True, path_type=Path), default=DEFAULT_STORE)
@click.option("--valid-only", is_flag=True, help="Only structurally valid laps (complete, no pit road, no reset).")
@click.option("--representative", "within", is_flag=False, flag_value=DEFAULT_WITHIN, default=None,
              type=float, help="Only laps within FRACTION of the best valid lap (default 0.05 if given bare).")
def list_laps(store_dir: Path, valid_only: bool, within: float | None):
    """List laps in the store."""
    store = ParquetLapStore(store_dir)
    try:
        _print_laps(store.list(valid_only=valid_only, within_best=within))
    finally:
        store.close()


if __name__ == "__main__":
    cli()
