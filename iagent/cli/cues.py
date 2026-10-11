"""`iagent cues`: the corner cue plan."""

import click

from iagent.cli.base import Ctx, emit


@click.group()
def cues():
    """Spoken corner cues for learning a track (what the live coach says approaching each corner)."""


def _cue_rows(plan) -> list[dict]:
    return [{"corners": c.corners, "target_m": round(c.target_m), "text": c.text, "short": c.short, "source": c.source}
            for c in plan.cues]


def _print_plan(plan) -> None:
    click.echo(f"{plan.track_key} / {plan.car_key}, following {plan.ref_lap_id}")
    for c in plan.cues:
        corners = "+".join(f"T{i}" for i in c.corners)
        click.echo(f"  {corners:<8} {c.target_m:7.0f} m  {c.text}" + ("" if c.source == "template" else f"  [{c.source}]")
                   + (f"\n  {'':<8} {'':>9}  then: {c.short}" if c.short else ""))


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
        emit({"path": str(path), "ref_lap_id": plan.ref_lap_id, "cues": _cue_rows(plan)})
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
        emit({"ref_lap_id": plan.ref_lap_id, "cues": _cue_rows(plan)})
    else:
        _print_plan(plan)


@cues.command("set")
@click.option("--track", required=True, help="Track key (see `iagent tracks`).")
@click.option("--car", required=True, help="Car key.")
@click.argument("corner", type=int)
@click.argument("text")
@click.option("--source", default="coach", show_default=True, help="Who wrote it: coach or driver.")
@click.option("--short", "short", help="The short form, said once the driver has heard TEXT a few times "
              "(default: none, TEXT every time).")
@click.pass_obj
def cues_set(ctx: Ctx, track: str, car: str, corner: int, text: str, source: str, short: str | None):
    """Rewrite the cue for CORNER (the cue covering it). Keep it short: it is spoken on the approach
    and must finish before the brake point; about 14 characters take a second to say. Once the
    driver knows the corner, a shorter reminder (--short) sounds less robotic than the full cue."""
    from iagent.live.cues import load_plan, save_plan

    plan = load_plan(ctx.workspace, track, car)
    if plan is None:
        raise click.ClickException(f"No cue plan for {track} / {car}: run `iagent cues build` first.")
    cue = plan.cue_for(corner)
    if cue is None:
        raise click.ClickException(f"No cue covers T{corner}.")
    cue.text, cue.source, cue.short = text.strip(), source, short.strip() if short else None
    save_plan(ctx.workspace, plan)
    click.echo(f"T{'+T'.join(map(str, cue.corners))}: {cue.text}" + (f"  (then: {cue.short})" if cue.short else ""))
