# Working on this repo (notes for coding agents)

[ARCHITECTURE.md](../ARCHITECTURE.md) is the source of truth for the design. Read it before a
structural change, and update it in the same change when the design moves (the code map, an
event or event handler table, the workspace layout, the CLI table).

This file is deliberately not a `CLAUDE.md`: the engineer and the lap review chat run `claude -p`
with their working directory at the workspace's parent, which is the repo root by default
(`iagent/agent.py`), so anything Claude Code loads automatically from the repo (`CLAUDE.md`,
`.claude/CLAUDE.md`, `.claude/rules/`) would reach the coach as well.

## Rules that aren't obvious from the code

- **No model call on the coach thread.** Anything that waits on `claude -p` goes through the
  engineer's queue and posts its answer back into the pipeline's inbox, with a fixed-text
  fallback.
- **Behaviour is definitions.** A new event, gate, cause, action or event handler is a declaration;
  if a change needs the core to know about a particular one, the design is wrong.
- **Event handlers act only on defined events.** Subclass `EventHandler` and mark methods with
  `@on`/`@enrich` for `Event` classes; a method marked for a class also gets its subclasses. So a
  subclass must really be a kind of its parent: put shared fields in an unnamed base (like
  `AtTheLine`) rather than subclassing an event whose handlers shouldn't see yours.
- **Strict schemas, no normalising.** Rules, engineer replies and CLI inputs are validated as
  given. Never add code that turns "T9" into 9 or guesses what a malformed value meant: reject it
  with an error that names the field and the allowed values.
- **One `Lap` type** (`iagent/laps/lap.py`) everywhere: segmented laps carry `frames`, listed
  ones don't. Don't add a parallel record type.
- **Lap store format changes** bump `SCHEMA_VERSION` in `iagent/laps/store.py` with a migration
  from the previous version; forcing a re-ingest loses the driver's Garage61 imports.

## Skills and the CLI are the coach's interface

- Skills (`coach/skills/*/SKILL.md`) use only the open Agent Skills frontmatter (`name`,
  `description`) and call tools by running `iagent ...` in a shell, never by a harness's tool
  names.
- A command's `--help` is the documentation the coach reads: keep it accurate when behaviour
  changes. Agent-facing commands take `--json`; workspace and rule problems raise
  `WorkspaceError` or `RuleError`, which the CLI turns into clean errors.
- Commands live in `iagent/cli/<group>.py`; shared output helpers in `iagent/cli/base.py`; a new
  group is registered in `iagent/cli/__init__.py`.

## Checking a change

- `uv run pytest` (about a minute; no sim, network or model). `tests/real/` runs only with
  recordings in `data/telemetry/`.
- The web UI builds with `npm --prefix web run build` into `iagent/ui/static/`.
