# iRacing Agent: an AI coach for learning tracks

An iRacing coach built as **skills for an existing agent harness** plus a **CLI that does the
telemetry work**. The first goal is helping a driver learn a new track: find where time is lost,
pick one thing to work on, and (later) cue it by voice at the right place on track.

> **Status: rewrite in progress (branch `rewrite/v2`).** Telemetry, laps, corner analysis and the
> first coaching skills work on recorded sessions. Live telemetry, rules and voice are next.
> The v1 two-agent LangGraph prototype has been removed; it lives on in the history of `main`.

The design lives in [.claude/architecture.md](.claude/architecture.md). In short:

- **No custom agent loop.** The coach runs inside an agent harness. Claude Code is the primary
  target (it works with a Claude subscription *or* local open models via Ollama); the skills use
  the open [Agent Skills](https://agentskills.io) format, so other harnesses can use them too.
- **Behaviour is markdown, capability is a CLI.** Skills in [coach/skills/](coach/skills/)
  describe how to coach; the `iagent` CLI computes the numbers (JSON output), so the model
  interprets rather than calculates.
- **No per-token API billing.** Claude through a subscription, or open-weight models run locally.
- **Agent-defined events (planned):** the agent creates rules and schedules through the CLI; a
  deterministic service on the sim PC fires them and wakes the agent. No LLM in the real-time loop.
- **Testable without the sim:** `.ibt` replay, a synthetic lap generator with exact ground truth,
  and regression tests against real recordings.

## Roadmap

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Telemetry sources (`.ibt` replay, synthetic), lap segmentation, lap store, pace filter | **Done** |
| 2 | Agent-facing CLI, `coach` plugin (`telemetry`, `lap-review` skills), headless harness test | **Done** |
| 3 | Corner map, per-corner metrics, comparison and consistency, corner names and track knowledge | **Done** |
| 4 | Live service: irsdk, agent-defined rules and schedules, backtesting, TTS, waking the agent | Next |
| 5 | Web UI: laps, corner comparisons, traces, rules, agent activity | |
| 6 | Push-to-talk voice, debrief and focus skills, memory across sessions, local-model evals | |

## Quick start

```bash
uv sync
uv tool install --editable .          # puts `iagent` on your PATH (or use `uv run iagent`)

# Ingest recordings (iRacing records .ibt with Alt+L, into Documents/iRacing/telemetry)
iagent ingest path/to/*.ibt
iagent tracks                          # track/car keys, lap counts, best times
iagent workspace                       # where laps and notes are stored
iagent laps list --track spa-2024-up --representative
iagent corners list --track spa-2024-up        # corner map, derived from your laps on first use
iagent corners compare 20250723-202727-L002    # corner by corner vs the fastest other valid lap
iagent corners consistency --track spa-2024-up # how repeatable each corner is
iagent laps trace 20250723-202727-L005 --from 250 --to 450 --channels Speed,Brake,Gear
```

Every command has `--help`; agent-facing ones take `--json` (`trace` prints CSV).

The workspace is `./workspace` unless you pass `--workspace` or set `IAGENT_WORKSPACE`:

```text
workspace/
  index.sqlite                          lap index
  laps/<track>/<car>/<lap_id>.parquet   raw 60 Hz samples (+ .grid.parquet on a 1 m grid)
  tracks/<track>/corners.json           corner map: geometry + names with their sources
  tracks/<track>/knowledge.md           what's known about each corner
  notes/<track>.md                      the coach's notes: focus, evidence, progress
```

No recordings? `iagent ingest synthetic --laps 7 --messy` generates laps with known ground truth:

```text
lap                   time  vs best  off(s)  valid  reasons
synthetic-0-L000         -              0.0  False  incomplete
synthetic-0-L001    58.317    +0.3%     1.0  True
synthetic-0-L002    58.223              0.0  False  pit_road
synthetic-0-L003    58.393              0.0  False  pit_road
synthetic-0-L004    84.750              0.0  False  discontinuity
synthetic-0-L005    58.135    +0.0%     0.0  True
synthetic-0-L006    58.353    +0.4%     1.0  True
```

### Corners

The corner map is derived from your own laps: a corner is a stretch of sustained lateral g, with
thresholds relative to the car's grip, so it works for any car. Chicanes split into one corner
per direction, double apexes merge, and the lap is tiled into one segment per corner so corner
time deltas add up to the lap delta. Names come separately: from you, CrewChief's landmark data
(`iagent corners landmarks`), or the coach's research, each with a recorded source.

On a real Spa recording (F4), after the coach named the corners:

```text
$ iagent corners compare 20250723-202727-L002
20250723-202727-L002 vs 20250723-202727-L005: +0.532s  (biggest losses: T16, T9, T7)
corner                     delta  brake  min kph  full thr  exit kph  off m (lap/ref)
T1 La Source              +0.131     -2     -4.9        +0      -5.9
...
T7 Bruxelles              +0.342     +0     -6.5         -      -4.2
T8                        -0.543      -     +1.1      -105     +25.3  0/39
T9 Pouhon                 +0.480      -     -0.9      +103     -20.1
...
T16 Bus Stop (exit)       +0.538      -     -6.1        -1      -6.3
```

Signs read from the driver's side: `brake` > 0 braked later, `min kph` > 0 carried more speed,
`full thr` > 0 back on full throttle later. `0/39` means the reference lap ran 39 m off track
there, so that "gain" is the reference's mistake.

### How laps are judged

- Lap times are interpolated across the start/finish crossing (sub-frame accurate; they match
  iRacing's own lap times on real recordings).
- **Valid** is structural: complete, no pit road, no position jump (reset/tow). The first lap of a
  recording is usually `incomplete`.
- **Representative** is about pace: valid laps within 5% of the best valid lap *for the same
  track layout and car*. Spins, recoveries and cool-downs fall out; a kerb clip that cost
  nothing stays in. Off-track time is shown (`off(s)`) but never disqualifies a lap.
- Laps are grouped by iRacing's internal track and car names (`spa-2024-up`, `formulair04`), so
  different layouts or cars are never compared.
- Each lap is stored raw (60 Hz) and on a 1 m distance grid, so laps compare point for point.

## Using the coach

Skills in [coach/skills/](coach/skills/):

| Skill | What it does |
| --- | --- |
| `telemetry` | How to find, inspect and compare laps and corners with the CLI, and how to read the numbers |
| `lap-review` | Reviews a session corner by corner, finds the most *repeatable* time loss, gives one focus and writes it to the notes |
| `name-corners` | Names the derived corners (driver, CrewChief, web, own knowledge, with confidence) and records track knowledge |

### Claude Code

```bash
claude plugin marketplace add /path/to/iracing-agent    # or <github-owner>/<repo>
claude plugin install coach@iracing-agent
```

Or, while developing, load it for one session: `claude --plugin-dir coach`. Then ask *"How did my
last Spa session go?"*. The coach names the track's corners if needed (`name-corners`), compares
your laps corner by corner (`lap-review`) and writes its focus to `workspace/notes/<track>.md`.

Headless (what the live service will do):

```bash
claude -p "How did my last session go?" --plugin-dir coach \
  --allowedTools "Bash(iagent *)" Read Write Edit --permission-mode acceptEdits
```

**Local models:** point Claude Code at Ollama (v0.14+) with
`ANTHROPIC_BASE_URL=http://<host>:11434 ANTHROPIC_AUTH_TOKEN=ollama claude --model <model>`.
Expect small models (what fits on a 16 GB Mac) to be much less reliable at multi-step tool use.

### Other harnesses

The skills are plain `SKILL.md` folders that only call the `iagent` CLI. Point any harness that
reads Agent Skills (Codex, OpenCode, Goose, ...) at `coach/skills/`.

## Layout

```text
.claude-plugin/marketplace.json   makes this repo a Claude Code plugin marketplace
coach/                            the coach plugin: skills/<name>/SKILL.md
iagent/
  telemetry/   frames, session info, sources (.ibt replay)
  laps/        segmentation, distance resampling, lap store, pace filter, recorder
  analysis/    corner map and metrics, CrewChief landmarks, splits, traces
  testing/     synthetic lap generator, .ibt writer
  cli.py       the `iagent` command
tests/        pytest suite (no sim, GPU, model or network needed)
```

## Tests

```bash
uv run pytest
```

`tests/real/` replays your own recordings from `data/telemetry/` (git-ignored) and checks our lap
times against iRacing's `LapLastLapTime`; those tests skip when the files are absent.

## Requirements

Python 3.12+ and [uv](https://docs.astral.sh/uv/). For the coach: Claude Code (with a Claude
subscription, or [Ollama](https://ollama.com) v0.14+ for local models) or another skills-capable
harness. iRacing itself is only needed for live telemetry (phase 4); everything else runs on
recorded `.ibt` files.

## License

All rights reserved.
(I may change my mind later. If you are keen to contribute - let me know!)
