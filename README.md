# iRacing Agent: an AI coach for learning tracks

An iRacing coach built as **skills for an existing agent harness** plus a **CLI that does the
telemetry work**. The first goal is helping a driver learn a new track: find where time is lost,
pick one thing to work on, and (later) cue it by voice at the right place on track.

> **Status: rewrite in progress (branch `rewrite/v2`).** Telemetry, laps, the analysis CLI and
> the first coaching skills work on recorded sessions. Live telemetry, voice and the UI are next.

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
| 3 | Corner map, per-corner metrics and comparison, corner names and track knowledge | Next |
| 4 | Live service: irsdk, agent-defined rules and schedules, backtesting, TTS, waking the agent | |
| 5 | Web UI: laps, corner comparisons, traces, rules, agent activity | |
| 6 | Push-to-talk voice, debrief and focus skills, memory across sessions, local-model evals | |

## Quick start

```bash
uv sync
uv tool install --editable .          # puts `iagent` on your PATH (or use `uv run iagent`)

# Ingest recordings (iRacing records .ibt with Alt+L, into Documents/iRacing/telemetry)
iagent ingest path/to/*.ibt
iagent tracks                          # track/car keys, lap counts, best times
iagent laps list --track spa-2024-up --representative
iagent laps compare 20250723-202727-L002      # vs the fastest other valid lap
iagent laps trace 20250723-202727-L005 --from 250 --to 450 --channels Speed,Brake,Gear
```

Laps live in `./workspace` unless you pass `--workspace` or set `IAGENT_WORKSPACE`. Every
command has `--help`; agent-facing ones take `--json`.

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

```text
$ iagent laps compare synthetic-0-L006 --sections 6
synthetic-0-L006 vs synthetic-0-L005: +0.217s  (biggest losses in sections [4, 5, 6])
sec   from     to   delta  min kph    ref  brake@    ref
  1      0    500  -0.011    202.8  205.1     481    485
  2    500   1000  -0.081    102.4   99.3       -      -
  3   1000   1500  +0.019    116.0  118.7    1091   1080
  4   1500   2000  +0.152     86.0   90.9    1771   1772
  ...
```

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

### Claude Code

```bash
claude plugin marketplace add /path/to/iracing-agent    # or <github-owner>/<repo>
claude plugin install coach@iracing-agent
```

Or, while developing, load it for one session: `claude --plugin-dir coach`. Then ask *"How did my
last Spa session go?"*. The `lap-review` skill finds the laps, compares them, traces the problem
area and writes a note to `workspace/notes/<track>.md`.

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
  analysis/    lap summaries, section comparison, traces
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

Python 3.12+ and [uv](https://docs.astral.sh/uv/). For the coach: Claude Code (subscription) or
another skills-capable harness. iRacing itself is only needed for live telemetry (phase 4).

## License

All rights reserved.
(I may change my mind later. If you are keen to contribute - let me know!)
