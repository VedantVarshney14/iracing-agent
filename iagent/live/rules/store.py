"""Where rules live: one JSON file each, `rules/<track_key>/<id>.json`, or `rules/_any/<id>.json`
for a rule on every track.

A rule's `status` says whether the live coach uses it: `draft` (just added or edited), `active`,
or `archived`. Editing a rule makes it a draft again, and activating needs a backtest of the rule
as it is now (`iagent.live.rules.backtest`), so nothing goes live that hasn't been seen against
real laps.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from iagent.live.rules import Rule, RuleError, check_against_map
from iagent.workspace import Workspace, WorkspaceError

ANY_TRACK = "_any"


def rules_root(root: Path) -> Path:
    return root / "rules"


def rule_path(root: Path, rule: Rule) -> Path:
    return rules_root(root) / (rule.track or ANY_TRACK) / f"{rule.id}.json"


def _files(root: Path) -> Iterator[Path]:
    """Every rule file in the workspace, in a stable order."""
    base = rules_root(root)
    if base.is_dir():
        yield from sorted(base.glob("*/*.json"))


def load_rules(
    root: Path,
    track: str | None = None,
    car: str | None = None,
    status: str | tuple[str, ...] | None = None,
) -> list[Rule]:
    """Rules for TRACK (and those for every track), optionally only with STATUS. Files that
    don't parse are skipped (`broken_rules` lists them)."""
    statuses = (status,) if isinstance(status, str) else status
    out = []
    for path in _files(root):
        if track is not None and path.parent.name not in (track, ANY_TRACK):
            continue
        try:
            rule = Rule.from_dict(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue
        if statuses and rule.status not in statuses:
            continue
        if car is not None and rule.car not in (None, car):
            continue
        out.append(rule)
    return out


def broken_rules(root: Path) -> list[tuple[Path, str]]:
    """Rule files that don't parse or validate, with the reason for each."""
    out = []
    for path in _files(root):
        try:
            Rule.from_dict(json.loads(path.read_text()))
        except (ValueError, OSError) as e:
            out.append((path, str(e)))
    return out


def find_rule(root: Path, rule_id: str) -> Rule:
    """The rule with this id, whatever its track or status. RuleError if there's none or it doesn't
    parse."""
    for path in _files(root):
        if path.stem == rule_id:
            try:
                return Rule.from_dict(json.loads(path.read_text()))
            except ValueError as e:
                raise RuleError(f"{path} doesn't parse: {e}") from None
    raise RuleError(f"No rule {rule_id!r}. See `iagent rules list`.")


def save_rule(root: Path, rule: Rule) -> Path:
    """Write the rule to its file, removing an older copy if it moved to another track (or to every
    track)."""
    path = rule_path(root, rule)
    for other in _files(root):  # a rule moved to another track (or to every track)
        if other.stem == rule.id and other != path:
            other.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rule.to_dict(), indent=2) + "\n")
    return path


def add_rule(
    root: Path, raw: dict, replace: bool = False, by: str = "coach"
) -> tuple[Rule, Path]:
    """Validate and save a rule as a draft. Corners are checked against the track's map."""
    raw = {k: v for k, v in raw.items() if k not in ("status", "meta")}
    rule = Rule.from_dict(raw)
    try:
        existing = find_rule(root, rule.id)
    except RuleError:
        existing = None
    if existing is not None and not replace:
        raise RuleError(
            f"A rule {rule.id!r} exists already: pass --replace to change it, or pick another id."
        )
    if rule.track is None and rule.corners():
        raise RuleError(f"{rule.id}: a rule about a corner needs a track.")
    if rule.track is not None and rule.corners():
        ws = Workspace(root)
        try:
            cmap = ws.corner_map(rule.track)
        except WorkspaceError as e:
            raise RuleError(f"{rule.id}: can't check its corners: {e}") from None
        finally:
            ws.close()
        check_against_map(rule, cmap)
    now = datetime.now(timezone.utc).isoformat()
    rule.meta = {
        "created_by": (existing.meta.get("created_by") if existing else None) or by,
        "created_at": (existing.meta.get("created_at") if existing else None) or now,
    }
    if existing is not None:
        rule.meta["edited_at"] = now
    rule.status = "draft"
    return rule, save_rule(root, rule)


def set_status(root: Path, rule_id: str, status: str, force: bool = False) -> Rule:
    """Make the rule draft, active or archived. Activating needs a backtest of the rule as it is
    now, unless FORCE."""
    rule = find_rule(root, rule_id)
    if status == "active" and not force:
        bt = rule.meta.get("backtest")
        if not bt or bt.get("fingerprint") != rule.fingerprint:
            raise RuleError(
                f"Backtest {rule_id} first (`iagent rules backtest {rule_id}`): rules go live only once "
                "they've been seen against real laps. (--force skips this.)"
            )
    rule.status = status
    rule.meta[f"{status}_at"] = datetime.now(timezone.utc).isoformat()
    save_rule(root, rule)
    return rule


def remove_rule(root: Path, rule_id: str) -> Path:
    """Delete the rule's file; returns where it was."""
    rule = find_rule(root, rule_id)
    path = rule_path(root, rule)
    path.unlink()
    return path


def signature(root: Path) -> tuple:
    """Changes when any rule file changes (the live coach reloads its rules then)."""
    out = []
    for path in _files(root):
        try:
            out.append((str(path), path.stat().st_mtime_ns))
        except OSError:
            pass
    return tuple(out)
