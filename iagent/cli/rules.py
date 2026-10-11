"""`iagent rules`: write, check, backtest and switch on rules for the live coach."""

import json
from pathlib import Path

import click

from iagent.cli.base import Ctx, emit


@click.group()
def rules():
    """Rules the coach sets for the live service: when to say what (or ask the engineer).

    A rule is JSON, validated against `iagent rules schema`: an event (`when`), an optional
    condition (`if`), actions and limits. Corners are the corner map's numbers (`iagent corners
    list --track T --json`). Add it (a draft), backtest it against recorded sessions, then activate
    it; the live coach picks up active rules, also mid-session. `iagent rules vars` lists what each
    event provides.

    \b
    iagent rules add '{"id": "pouhon-wide", "track": "spa-2024-up",
      "when": {"event": "corner_exit", "corner": 10}, "if": "off_track_m > 5", "in_a_row": 2,
      "actions": [{"say": "Pouhon: wide twice now. Tighter entry."}]}'
    iagent rules backtest pouhon-wide
    iagent rules activate pouhon-wide
    """


def _read_rule(text: str) -> dict:
    """A rule's JSON from the argument: inline, `@file`, or `-` for stdin."""
    if text == "-":
        text = click.get_text_stream("stdin").read()
    elif text.startswith("@"):
        text = Path(text[1:]).read_text()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"The rule isn't valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise click.ClickException("A rule is a JSON object.")
    return raw


def _when(rule) -> str:
    extra = " ".join(f"{k}={v}" for k, v in rule.when.items() if k != "event")
    return rule.trigger + (f" ({extra})" if extra else "")


def _bt_line(bt: dict | None, fingerprint: str) -> str:
    """One line on a rule's last backtest, and whether it was of the rule as it is now."""
    if not bt:
        return "not backtested"
    stale = "" if bt.get("fingerprint") == fingerprint else " (before the last edit)"
    return f"backtest: fired {bt.get('fired', 0)}x on {bt.get('laps_fired', 0)}/{bt.get('laps', 0)} laps{stale}"


@rules.command("add")
@click.argument("rule_json")
@click.option("--track", help="Track key (if the JSON has none). Without one the rule applies on every track.")
@click.option("--car", help="Only with this car.")
@click.option("--replace", is_flag=True, help="Change an existing rule (it becomes a draft again).")
@click.option("--by", default="coach", show_default=True, help="Who wrote it: coach or driver.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def rules_add(ctx: Ctx, rule_json: str, track: str | None, car: str | None, replace: bool, by: str, as_json: bool):
    """Add a rule from RULE_JSON (JSON text, @file.json or - for stdin), as a draft.

    It's checked now (fields, variables, corner names against the track's map) so mistakes show
    up here, not on track. Next: `iagent rules backtest ID`, then `iagent rules activate ID`.
    """
    from iagent.live.rules.store import add_rule

    raw = _read_rule(rule_json)
    if track:
        raw["track"] = track
    if car:
        raw["car"] = car
    from iagent.live.crewchief import overlaps

    rule, path = add_rule(ctx.workspace, raw, replace=replace, by=by)
    overlap = overlaps(rule)
    if as_json:
        emit({"path": str(path), "rule": rule.to_dict(), "crewchief_overlap": overlap})
        return
    if overlap:
        click.echo(f"Note: {overlap} Drivers running CrewChief would hear it twice.", err=True)
    click.echo(f"{rule.id}: {_when(rule)}" + (f" if {rule.condition.text}" if rule.condition else "")
               + f" -> {', '.join(a.key for a in rule.actions)}  [draft]")
    click.echo(f"Saved {path}. Next: iagent rules backtest {rule.id}")


@rules.command("list")
@click.option("--track", help="Rules for this track (and those for every track).")
@click.option("--status", type=click.Choice(["draft", "active", "archived"]), help="Only with this status.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def rules_list(ctx: Ctx, track: str | None, status: str | None, as_json: bool):
    """The rules, with their status and last backtest."""
    from iagent.live.rules.store import broken_rules, load_rules

    found = load_rules(ctx.workspace, track, status=status)
    broken = broken_rules(ctx.workspace)
    if as_json:
        emit({"rules": [r.to_dict() for r in found], "broken": [{"path": str(p), "error": e} for p, e in broken]})
        return
    if not found:
        click.echo("No rules." + ("" if status else " Add one with `iagent rules add`."))
    for r in found:
        click.echo(f"{r.id:<28} {r.status:<8} {r.track or 'any track':<18} {_when(r)}")
        if r.description:
            click.echo(f"{'':<29}{r.description}")
        click.echo(f"{'':<29}{_bt_line(r.meta.get('backtest'), r.fingerprint)}")
    for path, error in broken:
        click.echo(f"Broken: {path}: {error}", err=True)


@rules.command("show")
@click.argument("rule_id")
@click.pass_obj
def rules_show(ctx: Ctx, rule_id: str):
    """A rule as JSON."""
    from iagent.live.rules.store import find_rule

    emit(find_rule(ctx.workspace, rule_id).to_dict())


@rules.command("vars")
@click.option("--event", "--trigger", "event", help="Only this event (and what every event has).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def rules_vars(event: str | None, as_json: bool):
    """What rules can use: events and their fields, the shared state, live channels, functions
    and actions. All of it comes from the definitions, so a new event shows up here by itself."""
    from iagent.live.rules import describe

    info = describe()
    if event:
        if event not in info["events"]:
            raise click.ClickException(f"No event {event!r}; there are: {', '.join(info['events'])}.")
        info["events"] = {event: info["events"][event]}
    if as_json:
        emit(info)
        return
    for name, d in info["events"].items():
        notes = (" (ignored while not pushing, with pushing_only)" if d["judged"] else "") + (
            f"; also takes: {', '.join(d['extras'])}" if d["extras"] else "")
        click.echo(f"{name}: {d['doc']}{notes}")
        for k, v in d["fields"].items():
            click.echo(f"  {k:<22} {v}")
    for title, key in (("state (on every event)", "state"), ("live channels (iRacing names)", "channels"),
                       ("functions", "functions"), ("actions", "actions")):
        click.echo(f"{title}:")
        for k, v in info[key].items():
            click.echo(f"  {k:<22} {v}")


@rules.command("schema")
def rules_schema():
    """The JSON Schema rules are validated against (generated from the live coach's events)."""
    from iagent.live.rules import rule_schema

    emit(rule_schema())


@rules.command("backtest")
@click.argument("rule_ids", nargs=-1, required=True)
@click.option("--car", help="Car key (default: the rule's, else the one driven most on its track).")
@click.option("--track", help="Track key (for a rule on every track).")
@click.option("--session", "sessions", multiple=True, help="A recorded session id (repeatable; default: the last few).")
@click.option("--ibt", "ibt", multiple=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Replay this .ibt instead (repeatable).")
@click.option("--last", default=3, show_default=True, help="How many recent sessions, by default.")
@click.option("--alone", is_flag=True, help="Without the other active rules (they normally compete for the voice).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_obj
def rules_backtest(ctx: Ctx, rule_ids: tuple[str, ...], car: str | None, track: str | None, sessions: tuple[str, ...],
                   ibt: tuple[Path, ...], last: int, alone: bool, as_json: bool):
    """Replay recorded sessions through the live coach with these rules: where they would have
    fired, what they'd have said, and whether it would have been said (or dropped, and why).

    The result is noted on each rule; `activate` needs a backtest of the rule as it is now.
    """
    from iagent.live.rules.backtest import backtest, record_backtest
    from iagent.live.rules.store import find_rule

    found = [find_rule(ctx.workspace, i) for i in rule_ids]
    tracks = {r.track for r in found if r.track} | ({track} if track else set())
    if len(tracks) != 1:
        raise click.ClickException("Backtest rules for one track at a time" + (" (pass --track)." if not tracks else "."))
    track = tracks.pop()
    cars = {r.car for r in found if r.car} | ({car} if car else set())
    if len(cars) > 1:
        raise click.ClickException("These rules are for different cars: backtest them separately.")
    car = cars.pop() if cars else _main_car(ctx, track)
    report = backtest(ctx.workspace, found, track, car, list(sessions) or None, list(ibt) or None, last,
                      with_active=not alone)
    for rule in found:
        record_backtest(ctx.workspace, rule, report["rules"][rule.id])
    if as_json:
        emit(report)
        return
    click.echo(f"{track} / {car}, cues following {report['ref_lap_id']}: {', '.join(report['sessions'])}")
    for rule in found:
        r = report["rules"][rule.id]
        t = r["totals"]
        click.echo(f"\n{rule.id}: fired {t['fired']}x on {r['laps_fired']} of {r['laps']} laps driven "
                   f"(trigger {t['occurrences']}x, not pushing {t['skipped']}, condition held {t['matched']}, "
                   f"held by limits {t['limited']})")
        if any(a.key == "say" for a in rule.actions):
            click.echo(f"  said {t['said']}, cut off {t['cut']}, dropped {t['dropped']}")
        for f in r["firings"][:40]:
            where = f"{f['lap_dist']:>5} m" if f.get("lap_dist") is not None else ""
            values = " ".join(f"{k}={v}" for k, v in f["values"].items())
            what = "; ".join(f"{k}: {v}" for a in f.get("actions", []) for k, v in a.items() if k in ("say", "ask_engineer", "log"))
            status = f["result"] if f["result"] == "fired" else f"limited ({f['why']})"
            click.echo(f"  {f['session']}  lap {f['lap_no'] + 1:>2}  {where}  {status}  {values}" + (f"\n      {what}" if what else ""))
        if len(r["firings"]) > 40:
            click.echo(f"  ... {len(r['firings']) - 40} more (--json for all)")
        for ln in r["lines"]:
            if ln["status"] != "said":
                click.echo(f"  {ln['status']}: {ln['text']}" + (f" ({ln['note']})" if ln.get("note") else ""))


def _main_car(ctx: Ctx, track: str) -> str:
    """The car with the most laps on TRACK."""
    counts: dict[str, int] = {}
    if (ctx.workspace / "index.sqlite").exists():
        for r in ctx.store().list(track=track):
            counts[r.car_key] = counts.get(r.car_key, 0) + 1
    if not counts:
        raise click.ClickException(f"No laps on {track}: pass --car.")
    return max(counts, key=counts.__getitem__)


@rules.command("activate")
@click.argument("rule_ids", nargs=-1, required=True)
@click.option("--force", is_flag=True, help="Without a backtest of the rule as it is now.")
@click.pass_obj
def rules_activate(ctx: Ctx, rule_ids: tuple[str, ...], force: bool):
    """Make rules live (a running coach picks them up within ~10 s)."""
    from iagent.live.rules.store import set_status

    for rule_id in rule_ids:
        rule = set_status(ctx.workspace, rule_id, "active", force)
        click.echo(f"{rule.id}: active")


@rules.command("deactivate")
@click.argument("rule_ids", nargs=-1, required=True)
@click.pass_obj
def rules_deactivate(ctx: Ctx, rule_ids: tuple[str, ...]):
    """Take rules out of the live coach (back to draft)."""
    from iagent.live.rules.store import set_status

    for rule_id in rule_ids:
        click.echo(f"{set_status(ctx.workspace, rule_id, 'draft').id}: draft")


@rules.command("archive")
@click.argument("rule_ids", nargs=-1, required=True)
@click.pass_obj
def rules_archive(ctx: Ctx, rule_ids: tuple[str, ...]):
    """Keep rules for the record, out of use."""
    from iagent.live.rules.store import set_status

    for rule_id in rule_ids:
        click.echo(f"{set_status(ctx.workspace, rule_id, 'archived').id}: archived")


@rules.command("remove")
@click.argument("rule_ids", nargs=-1, required=True)
@click.pass_obj
def rules_remove(ctx: Ctx, rule_ids: tuple[str, ...]):
    """Delete rules."""
    from iagent.live.rules.store import remove_rule

    for rule_id in rule_ids:
        click.echo(f"Removed {remove_rule(ctx.workspace, rule_id)}")
