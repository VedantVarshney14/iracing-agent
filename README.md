# iRacing Agent: an AI coach for learning tracks

An iRacing coach built as **skills for an existing agent harness** plus a **CLI that does the
telemetry work**. The first goal is helping a driver learn a new track: find where time is lost,
pick one thing to work on, and (later) cue it by voice at the right place on track.

> **Status:** works today on recorded sessions: telemetry, laps, corner analysis, Garage61
> reference laps and ghosts, and the coaching skills. Live telemetry, rules and voice are next.

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
| 3b | Garage61 reference laps (own and teammates'), iRacing ghost download and install | **Done** |
| 4 | Live service: irsdk, agent-defined rules and schedules, backtesting, TTS, waking the agent | Next |
| 5 | Web UI: lap review against a ghost, racing lines, coach chat, session library | In progress |
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
  tracks/<track>/track.json             iRacing ids, names and length (from ingest)
  reference/                            other drivers' laps (e.g. Garage61), kept apart from yours
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
- The car's position (`Lat`/`Lon`) is stored when the source has it: `.ibt` recordings and
  Garage61 exports do, iRacing's live telemetry doesn't. It is what racing-line views are drawn
  from. Workspaces ingested before it was added: re-run `iagent ingest` on the same files.

### Reference laps and ghosts from Garage61

Compare against faster drivers' laps from [Garage61](https://garage61.net), and drive against
their ghosts in iRacing. Create a personal access token at https://garage61.net/developer and
provide it as `GARAGE61_TOKEN` (or `GARAGE61_PAT`) in the environment or a `.env` file in the
directory you run `iagent` from, or save it to `~/.config/iagent/garage61.token`.

```bash
iagent garage61 status                         # token check, your teams
iagent garage61 find --track okayama-full      # best lap per driver, same layout and car
iagent garage61 import 01K...B
iagent corners compare 20260920-180131-L005 g61-teammate-b-xxxxxx-L000
iagent garage61 ghost 01K...B --install   # on the sim PC
```

```text
okayama-full / formulair04: your best 93.340s
garage61 id                  driver                      time  vs you rating date       track °C telemetry ghost
01K...A                      Teammate A                90.918   -2.6%   1163 2026-05-27     39.4       yes    no
01K...B                      Teammate B                91.636   -1.8%   1190 2026-05-27     37.8       yes   yes
```

- A personal token reaches **your own and your Garage61 teammates'** laps (Garage61 only lets
  approved applications search everyone's).
- Reference laps are stored separately and never count as your best. Laps are matched by iRacing
  track and car id, recorded when you ingest your own laps (re-run `iagent ingest` on older
  workspaces).
- `ghost` downloads the lap's iRacing ghost file (`.blap`); `--install` copies it into iRacing's
  `Documents/iRacing/lapfiles/<track>` folder without touching your own best-lap file. In iRacing:
  Options > Driving Aids > Load Comparison Lap, and tick "Display Reference Car".

## Lap review in the browser

`iagent ui` opens a lap review in your browser:
- **Track map.** Your line, coloured by the time you gained or lost in each corner. The
  **Corner** view overlays your line on the ghost's, with brake and full-throttle points.
- **Corner table.** Each corner's comparison against the ghost.
- **Traces.** Gap to ghost, speed, throttle, brake, gear and steering against distance. Hover
  to follow both cars on the map, drag to zoom, double-click to reset.

- **Differences made visible.** Traces are shaded blue where you're ahead of the ghost and orange
  where you're behind, a "time lost per 10 m" bar chart shows where the gap grows, and zooming into
  a corner marks both laps' brake and full-throttle points. Chips toggle speed difference,
  steering and line offset.
- **iRacing on this machine.** On the sim PC `iagent ui` watches Documents/iRacing/telemetry and
  ingests each recording when the session ends (header pill; `--telemetry-dir`,
  `IAGENT_TELEMETRY_DIR` or `--no-watch` to change that). A Garage61 ghost can be installed into
  iRacing in one click; elsewhere (e.g. a Mac) the button downloads the `.blap` to copy over.
- **Corner view.** One corner at a time (open it from the corner card, or the coach opens it):
  both racing lines with the sideways gap exaggerated ×3/×5/×10 (distance along the track stays
  true), a gap ladder, your line coloured by speed against the ghost, the corner's numbers and its
  traces, shaded where you're ahead or behind, including how far your line runs wider or tighter.
- **Coach chat.** Ask about the lap beside the analysis. The coach is your own Claude Code
  (`claude -p` with the coach plugin: your subscription, no API key) and knows what's on screen.
  When it talks about a corner it points at it, running `iagent ui show --corner 9 --view corner`
  to select, zoom and switch the map. Set `IAGENT_CLAUDE` if `claude` isn't on your PATH, and
  `IAGENT_COACH_MODEL` to pick a model.

The ghost picker lists your laps and your Garage61 teammates' laps for the track and car (with a
Garage61 token, see below). A Garage61 lap is imported when you pick it. The ghost defaults to the
fastest Garage61 lap, imported on first open, otherwise your next-best lap. The URL (lap, ghost,
corner, view) can be bookmarked.

The UI is a React app in [web/](web/). Build it once (needs [Node.js](https://nodejs.org) 20+), then run:

```bash
npm --prefix web install && npm --prefix web run build   # writes iagent/ui/static/
iagent ui                                                 # http://127.0.0.1:8765, Ctrl+C to stop
```

While working on the UI, run `iagent ui --no-browser` and `npm --prefix web run dev` (hot
reload on http://localhost:5173, API calls forwarded to the server).

## Using the coach

Skills in [coach/skills/](coach/skills/):

| Skill | What it does |
| --- | --- |
| `telemetry` | How to find, inspect and compare laps and corners with the CLI, and how to read the numbers |
| `lap-review` | Reviews a session corner by corner, finds the most *repeatable* time loss, gives one focus and writes it to the notes |
| `name-corners` | Names the derived corners (driver, CrewChief, web, own knowledge, with confidence) and records track knowledge |
| `reference-laps` | Picks a faster Garage61 lap (yours or a teammate's), coaches from the corner-by-corner difference, and installs its ghost for iRacing |

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
  analysis/    corner map and metrics, CrewChief landmarks, splits, traces, position
  references/  Garage61 client, CSV import, iRacing ghost files
  testing/     synthetic lap generator, .ibt writer
  ui/          `iagent ui`: the local API server and what the screens show (review.py)
  workspace.py lap stores, reference laps and corner maps, shared by the CLI and the UI
  cli.py       the `iagent` command
web/          the browser UI (React + Vite + TypeScript), built into iagent/ui/static/
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
harness. For the browser UI: Node.js 20+ to build it. iRacing itself is only needed for live
telemetry (phase 4); everything else runs on recorded `.ibt` files.

## License

All rights reserved.
(I may change my mind later. If you are keen to contribute - let me know!)
