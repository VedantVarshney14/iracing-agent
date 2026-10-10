# iRacing Agent: an AI coach for learning tracks

An iRacing coach built as **skills for an existing agent harness** plus a **CLI that does the
telemetry work**. The first goal is helping a driver learn a new track: find where time is lost,
pick one thing to work on, and (later) cue it by voice at the right place on track.

> **Status:** works on recorded sessions (telemetry, laps, corner analysis, Garage61 reference
> laps and ghosts, coaching skills) and live: spoken corner cues and feedback on track, rules the
> coach sets and backtests, debriefs and wake-ups in the coach's own words, and a browser UI.
> Push-to-talk voice input is next.

How it's built is explained in [ARCHITECTURE.md](ARCHITECTURE.md) (diagrams, the live pipeline,
rules, the engineer, how to extend it); the design notes are in
[.claude/architecture.md](.claude/architecture.md). In short:

- **No custom agent loop.** The coach runs inside an agent harness. Claude Code is the primary
  target (it works with a Claude subscription *or* local open models via Ollama); the skills use
  the open [Agent Skills](https://agentskills.io) format, so other harnesses can use them too.
- **Behaviour is markdown, capability is a CLI.** Skills in [coach/skills/](coach/skills/)
  describe how to coach; the `iagent` CLI computes the numbers (JSON output), so the model
  interprets rather than calculates.
- **No per-token API billing.** Claude through a subscription, or open-weight models run locally.
- **One event pipeline, built like a race team.** The live coach is a pipeline of typed events
  and small components; everything it says goes through rules (its own built-in ones, and the
  agent's, which are backtested before they go live). No model in the real-time loop. A race
  engineer (one Claude Code conversation per session) hears the radio, answers questions and
  wake-ups, debriefs, and writes notes for the next session.
- **Testable without the sim:** `.ibt` replay, a synthetic lap generator with exact ground truth,
  and regression tests against real recordings.

## Roadmap

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Telemetry sources (`.ibt` replay, synthetic), lap segmentation, lap store, pace filter | **Done** |
| 2 | Agent-facing CLI, `coach` plugin (`telemetry`, `lap-review` skills), headless harness test | **Done** |
| 3 | Corner map, per-corner metrics, comparison and consistency, corner names and track knowledge | **Done** |
| 3b | Garage61 reference laps (own and teammates'), iRacing ghost download and install | **Done** |
| 4 | Live service: irsdk, agent-defined rules and schedules, backtesting, TTS, waking the agent | In progress (corner cues, feedback, TTS, rules + backtests) |
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

By default, the workspace is the `workspace/` directory in the project, regardless of the
directory you run `iagent` from. Pass `--workspace` or set `IAGENT_WORKSPACE` to use another
location:

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
- **Library.** Where laps come from, every track you've driven, and each session's laps with a
  Review and a Ghost choice (your laps or Garage61 teammates'). On the sim PC it shows the watched
  telemetry folder; on a Mac, drop `.ibt` files onto it or point it at a folder synced from the
  sim PC (OneDrive, Dropbox, a network share), which is then watched like iRacing's own. It also
  shows whether Garage61 is connected (a token can be added there) and whether `claude` was found.
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

`iagent ui` also starts when the workspace has no laps yet, on the library: drop recordings
there, or wait for the watched folder to bring them in. The watched folder is the one last chosen
in the library (saved in the workspace's `settings.json`), else `IAGENT_TELEMETRY_DIR`, else
`Documents/iRacing/telemetry`; `--telemetry-dir` overrides all three. `iagent ingest <file.ibt>`
still works from the command line.

The server only answers on localhost (pass `--host 0.0.0.0` to open it from another machine) and
refuses POSTs without the web app's `X-Iagent` header, so other sites open in your browser can't
start the coach or write to the workspace.

## Coaching while you drive

`iagent live run` coaches you on track, without a model in the loop. It's meant to sound like
an engineer on the radio, not a recording: brief when you're busy, fuller when you have time.

- **Corner cues.** Approaching each corner: *"La Source, hairpin right. Hard brake, second
  gear."*, timed to finish at the reference lap's brake point at your current speed. Corners close
  together share a cue. The first two laps cue every corner; after that only the corners that
  went badly, so it goes quiet as you learn. Once you've heard a cue in full, it shortens to a
  reminder: *"La Source. Hard brake."*
- **Feedback.** After a corner that cost time, on the next straight: *"Turn 5: braked 20 metres
  early. Brake later."*, and a hint in that corner's cue next lap (*"Brake later than last
  lap."*). The same mistake again is a repeat (*"Turn 5 again: ..."*), and fixing it gets a
  *"Turn 5: better."* Laps off the pace (out laps, cool-downs, a spin) are ignored.
- **More when there's time.** Feedback has a longer version (the why and the how: *"you're
  braking about 20 metres before the reference. There's more room than it feels: move the brake
  point a few metres a lap..."*), said instead when you're not pushing. On a cool-down lap (15 s
  or more off the pace, not just a moment) the coach debriefs: the corner costing the most over
  your last laps at pace, what to change and how, and how consistent the laps were.
- **An engineer for the session.** Where the model has time to answer, it does the talking, and
  it's the same conversation all session, like one engineer on the radio: your Claude Code
  (`claude -p`) gives a radio check and the plan as you head out (from its notes), hears every cue
  and lap time, answers your questions and rules that `wake` it, words the cool-down debrief
  (asked as you slow down, said if it answers in time: ~5 s in testing, wanted ~15 s in), and
  writes up its notes (`workspace/notes/<track>.md`) when the session ends; the next session's
  engineer starts from them. It's given the numbers, not asked to work them out. Corner cues stay
  fixed text, because they must be instant and exactly timed, but the coach can rewrite them
  between sessions (`iagent cues set ... --short ...`). No reply in time, or no `claude`: the coach's
  own phrasing is said. `--no-narrate` turns it off.
- **One focus at a time.** After the learning laps the corner losing the most (over the last two
  laps at pace) becomes the focus: *"Focus now: Turns 15 and 16. Just a lift, no brakes."* It's
  cued every lap; other corners speak up only for a big loss or an off. Once you match the
  reference there twice, the next focus is picked.
- **Only while you're pushing.** Pace over the last 400 m is compared with your own best lap. On
  an out lap, a cool-down or just after a moment (10%+ slower) the coach goes quiet and doesn't
  judge the corners, then picks up again once you're within 5%, so one moment doesn't write off
  the rest of the lap.
- **Lap summary** at the line, short enough for a short straight: *"2 27.0, 5 tenths down."*, or
  where it went on a lap with a moment: *"1 44.2. Lost it at Turn 7."*

**From the browser:** the **Coaching** tab in `iagent ui` starts the coach (iRacing, or a replay
of a recording) and, afterwards, reviews each coached session:
- which laps you were pushing, and where a moment or a tranquille stretch was;
- where the time went (a map of the average loss per corner while pushing);
- **did the advice work?** each piece of advice with that corner before and after, judged on the
  laps you were pushing ("working", "mixed", "not yet"), and how the focus went;
- everything the coach said, lap by lap, including what it held back and why, with links into the
  corner view;
- a debrief written by the coach (`claude -p`), questions about the session, and **the next
  session's plan**: pick its focus and edit that corner's cue. A planned focus starts the next
  two sessions at that track, and is re-checked after two pushing laps, so it can't get stuck on
  something already sorted.

Each session's log (and debrief) is kept in `workspace/sessions/live/`.

Cues follow the fastest Garage61 teammate lap for the track and car, else your own best, and work
on a track you've never driven if a teammate's lap is imported. Speech is
[Pocket TTS](https://github.com/kyutai-labs/pocket-tts) on the CPU (2 threads).

**With CrewChief.** CrewChief does the race engineer's job (spotter, lap times and personal
bests, gaps, fuel, tyres, flags, pit calls); the coach does technique and leaves all of that to
it. It never talks over the spotter (nothing new starts with a car alongside), and when
CrewChief is running (`--crewchief auto`, the default, detects it; `on`/`off` to force it) the lap
summary drops the lap time and gap and says only where the lap went (*"Most time lost at Pouhon."*),
and for a few seconds after the line only corner cues are said, while CrewChief reads the time.
`iagent rules add` warns about rules that would say what CrewChief already does.

```bash
uv sync --extra voice                                    # Pocket TTS + audio output (PyTorch, CPU)
iagent cues build --track spa-2024-up --car formulair04  # see / rebuild the cues (built on first use)
iagent cues set --track spa-2024-up --car formulair04 1 "La Source. Hairpin right. Big stop, second gear." \
  --short "La Source. Big stop."                         # the reminder once it's been heard
iagent live run                                          # on the sim PC: waits for iRacing
iagent live run --replay session.ibt --start 700         # anywhere: hear a recording in real time
iagent live run --replay session.ibt --print --speed 20  # what it would say, fast, no audio
```

### Rules: what else to watch for

Everything the live coach says goes through **rules**: an event, a condition and what to do,
run with no model involved. Its own cues, feedback, summaries and debriefs are built-in rules;
the coach (or you) can add more. The `live-rules` skill writes them.

```bash
iagent rules add '{"id": "pouhon-wide", "track": "spa-2024-up",
  "when": {"corner_exit": "Pouhon"}, "if": "off_track_m > 5", "in_a_row": 2,
  "action": {"say": "Pouhon: wide twice now. Tighter entry."}}'
iagent rules backtest pouhon-wide        # replay recent sessions: where it fires, what's said
iagent rules activate pouhon-wide        # live (a running coach picks it up within ~10 s)
iagent rules list
iagent rules vars                        # every event, its fields, the shared state, functions
```

- **Events:** `when` names any event the live coach knows (`{"event": "corner_exit", "corner":
  "Pouhon"}`), with filters on its fields. Among them: a point on track (`{"at": "T9"}`: timed to
  finish before the reference brake point, or `"T9 apex"`, metres, `lead_s`, `offset_m`), a corner
  exit with that corner's numbers against the reference (`brake_diff_m`, `min_speed_diff_kph`,
  `off_track_m`, ...), lap complete (`new_best`, `gap_s`, ...), pit entry/exit, pace or focus
  changes, a cool-down, a condition on live channels (`{"condition": "speed_kph > 280"}`,
  edge-triggered), and schedules (`every_laps`, `every_s`, ...). `iagent rules vars` lists them
  all, generated from their definitions.
- **Conditions and text** use a small, safe expression language: arithmetic, comparisons,
  `and`/`or`/`not`, `in`, and helpers like `round5()` and `say_time()`. Spoken text has
  `{expression}` holes. A missing value (a corner taken without braking has no `brake_m`)
  makes the condition false rather than failing.
- **Actions:** `say` (cue, feedback or summary priority, through the same speech rules as the
  cues; add `"long"` for a fuller version said when you're not pushing), `log`, and `wake`
  (the coach answers on the radio in its own words, or stays silent).
- **Limits:** cooldowns, per-lap and per-session caps, `once`, `in_a_row`. Rules ignore out
  laps, cool-downs and moments unless `"pushing_only": false`.
- **Safe to change:** a new or edited rule is a draft until it has been backtested and activated.
  Rules live in `workspace/rules/<track>/` (or `rules/_any/` for every track). Each coached
  session's log records every firing, and the Coaching tab shows rule lines.

## Using the coach

Skills in [coach/skills/](coach/skills/):

| Skill | What it does |
| --- | --- |
| `telemetry` | How to find, inspect and compare laps and corners with the CLI, and how to read the numbers |
| `lap-review` | Reviews a session corner by corner, finds the most *repeatable* time loss, gives one focus and writes it to the notes |
| `name-corners` | Names the derived corners (driver, CrewChief, web, own knowledge, with confidence) and records track knowledge |
| `reference-laps` | Picks a faster Garage61 lap (yours or a teammate's), coaches from the corner-by-corner difference, and installs its ghost for iRacing |
| `live-rules` | Writes rules for the live coach (what to watch for on track and what to say), backtests them and activates them |

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
  agent.py     the agent harness: Agent (abstract), ClaudeCode (`claude -p`)
  live/        the live coach: pipeline + events + state (typed), components/, rules/ (schema,
               actions, engine) + builtin_rules, phrasing, speech, engineer + narrator (radio),
               rulebook (backtests), cues, sources, sessions and reports
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
