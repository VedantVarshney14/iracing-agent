# Architecture (v2)

Status: phases 1–3 are implemented (telemetry sources, laps, agent-facing CLI and skills, corner
analysis); live telemetry, rules, voice and UI are design. Supersedes the two-agent LangGraph design.

## 1. Goals

**Primary problem: help the driver learn a new track.** First test bed: Spa-Francorchamps.
Race-craft, strategy and fuel come later on the same foundations.

1. **Don't build an agent harness.** Plug into an existing one. Claude Code is the primary
   target; the repo stays usable from any harness that reads Agent Skills and can run a shell.
2. **Behaviour lives in skills** (markdown), **capability lives in CLIs** (`iagent ...`, JSON
   output). Changing how the coach coaches means editing a `SKILL.md`, not code.
3. **No per-token API billing.** Models are either Claude through a Claude subscription (Claude
   Code only) or open-weight models run locally.
4. **The agent defines its own events**: it creates rules and schedules through the CLI; a
   deterministic service evaluates them and wakes the agent.
5. **Fully testable without the sim** (section 10).

Non-goals (for now): replacing CrewChief's spotter/fuel calls, VR UI, multi-driver scenarios.

## 2. What "learning a track" means

1. **Map**: derive the corner map from recorded laps; attach names from researched knowledge.
2. **Compare**: per corner, driver vs reference: brake point, minimum speed, throttle pickup,
   and *consistency* across recent laps.
3. **Focus**: one thing at a time ("this session: Bus Stop brake point, 15 m later").
4. **Cue**: deliver it at the right place on track with a short spoken cue.
5. **Debrief**: between runs, analyse and update the plan.
6. **Remember**: persist progress and the driver's notes across sessions.

Success measures: lap-time delta to reference; per-corner brake-point spread (m) shrinking over a
session; time until N corners are within tolerance.

## 3. System overview

Everything runs on the sim PC except, optionally, the model.

```text
 Sim PC (Windows)                                                       model
┌──────────────────────────────────────────────────────────────┐
│ iagent service   (always on, deterministic, no LLM)          │
│   telemetry (irsdk) → lap segmenter → lap store              │
│   rule engine + scheduler ── events ──┐                      │
│   push-to-talk STT ── utterances ─────┤                      │
│   speech arbiter + TTS ◄── say ───────┼──────┐               │
│   web UI                              ▼      │               │
│                         ┌─────────────────────────────┐      │
│                         │ agent harness (Claude Code) │──────┼──► Claude (subscription)
│                         │   skills/*.md               │      │    or Ollama (Mac / local)
│                         │   runs `iagent ...` CLIs    │      │
│                         │   reads/edits workspace/    │      │
│                         └─────────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
```

- The **service** owns everything time-critical: telemetry, laps, rules, audio. It never waits on
  a model. Planned cues fire even if the model is slow or unreachable.
- The **harness** is woken per event (lap complete, rule fired, utterance, schedule), never per
  frame. It acts only through the CLI and the workspace files.
- The only network hop is harness → model, over the harness's own protocol. The Mac's role, if
  any, is to run `ollama serve`.
- For development, the same pieces run on the Mac against recorded `.ibt` files.

## 4. Harness

**Primary: Claude Code.** It is the only harness a Claude subscription may be used in (Anthropic
restricted subscription OAuth to Claude Code and claude.ai in Feb 2026, enforced Apr 2026), and it
can also drive open models through Ollama's Anthropic-compatible endpoint
(`ANTHROPIC_BASE_URL=http://<host>:11434`). It already provides skills with progressive loading,
shell access, file editing, hooks, headless runs (`claude -p`) and session resume.

**Harness-agnostic by construction.** Nothing in the repo depends on Claude Code beyond packaging:

- Skills use only the open Agent Skills format (`name`, `description` frontmatter + markdown).
  Claude Code-specific frontmatter is avoided; if ever needed it is optional.
- Skills call tools by running `iagent ...` in a shell, never by harness-specific tool names.
- Memory is plain files in the workspace.
- Waking the agent is one adapter in the service: "send this message to the coach session". The
  Claude Code adapter runs `claude -p --resume <session> <message>`; other adapters (Codex,
  OpenCode, Goose) are the same few lines with a different command.

**Models.** Claude via subscription is the practical primary. Local open models are a supported
fallback but must pass the eval suite (section 10) first: a coding-agent harness has a large
prompt, and on this hardware (M1 16 GB; the 3070 belongs to the sim) only ~8–20B models fit, which
are unreliable at multi-step tool use. Subscription usage limits are respected by waking the agent
per lap/event, never per frame, and keeping in-lap cues deterministic.

## 5. Repository as a skills repository

```text
.claude-plugin/marketplace.json   # makes the repo a Claude Code marketplace
coach/                            # the plugin (harness-agnostic content)
  .claude-plugin/plugin.json
  skills/
    telemetry/SKILL.md            # how to find, inspect and compare laps and corners
    lap-review/SKILL.md           # review a session's laps and give one focus
    name-corners/SKILL.md         # name derived corners, record track knowledge
    (planned) pick-focus, write-cue, debrief, set-trigger
iagent/                           # Python package; installs the `iagent` CLI
workspace/                        # per-user data (git-ignored); the agent's working dir
```

Install for Claude Code: `claude plugin marketplace add <repo>` then
`claude plugin install coach@iracing-agent`, or `claude --plugin-dir coach` during development.
Other harnesses: point their skills directory at `coach/skills/`.

**CLI contract.** Every command an agent uses supports `--json` (or emits CSV for bulk traces),
has a `--help` that is accurate enough to be the documentation, and finds the workspace from
`--workspace` or `IAGENT_WORKSPACE`. Errors go to stderr with a non-zero exit code.

| Command | Purpose | Status |
| --- | --- | --- |
| `iagent ingest <file.ibt>` | segment a recording into the lap store | done |
| `iagent tracks` | tracks/cars in the store, lap counts, best times | done |
| `iagent laps list` | laps with validity, pace, filters | done |
| `iagent laps show <id>` | lap summary and distance splits | done |
| `iagent laps compare <id> [ref]` | time gained/lost per section vs a reference | done (sections; corners later) |
| `iagent laps trace <id>` | channel samples over a distance range (CSV) | done |
| `iagent corners map/list/name` | derive, show and name the corner map | done |
| `iagent corners landmarks` | CrewChief corner-name hints matched to the map | done |
| `iagent corners report/compare` | per-corner metrics for a lap, and vs a reference | done |
| `iagent corners consistency` | per-corner spread across representative laps | done |
| `iagent rules add/backtest/activate/list` | agent-defined triggers | phase 4 |
| `iagent schedule add` | time/lap-based wake-ups | phase 4 |
| `iagent say "<text>"` | speak through the arbiter | phase 4 |
| `iagent live snapshot` | current channel values | phase 4 |
| `iagent service start/status` | run the service | phase 4 |

## 6. Data model

### Frames

A timestamped mapping of channels, sampled at 60 Hz. Time is `SessionTime`, never the wall clock,
so replays are deterministic and can run faster than real time (a 30-minute session replays in
under a second).

### Laps and the lap store

- One Parquet file per lap holding the raw samples, plus a copy resampled onto a **1 m distance
  grid** so laps compare point for point. Parquet is for exact dtypes and small files, not scale.
  Access goes through the `LapStore` protocol; the format is an implementation detail.
- SQLite index: lap id, session, track, car, sim lap number, lap time, completeness, validity,
  reasons, off-track seconds, source.
- **Identity.** Laps are grouped by `track_key` (iRacing's internal `TrackName`, e.g.
  `spa-2024-up`, unique per layout and scan version) and `car_key` (the driver's `CarPath`, e.g.
  `formulair04`). Display names are kept for people. Never compare laps across keys.
- **Validity vs pace.** `valid` is structural only: complete, no pit road, no position jump.
  Off-track time is recorded but never disqualifies a lap. *Representative* laps are valid laps
  within a tolerance (default 5%) of the best valid lap for the same track and car. On real
  Okayama, Spa and Watkins Glen recordings this separates normal laps from excursions and slow
  laps, and keeps a best lap that had a brief off-track.

### Workspace

```text
workspace/
  laps/<track_key>/<car_key>/<lap_id>.parquet   # + .grid.parquet
  index.sqlite
  tracks/<track_key>/corners.json               # derived geometry + names (agent-editable)
  tracks/<track_key>/knowledge.md               # researched names, quirks, sources
  cache/                                        # downloaded reference data (CrewChief landmarks)
  notes/driver.md                               # who the driver is, goals, preferences
  notes/<track_key>.md                          # per-track learnings, current focus
  notes/sessions/<date>.md                      # debrief summaries
  rules/<track_key>/*.json                      # active + archived rules
  scratch/                                      # agent's free space (analysis scripts, plots)
```

Memory is markdown the agent reads and edits with its harness's normal file tools.

### Track knowledge

Two layers:

1. **Geometry (derived)**: the corner map (section 7), saved as `corners.json`.
2. **Names and knowledge (attached)**: each corner carries an optional name with its source
   (`driver`, `crewchief`, `web`, `model`) and confidence; names survive re-mapping. Character
   and quirks go in `knowledge.md`. Sources by trust: the driver's corrections; CrewChief's
   MIT-licensed `trackLandmarksData.json` (about 25 iRacing tracks, last updated 2019, names
   sometimes misspelled or generic; its Spa distances match our `LapDist` to within ~50 m);
   web search; the model's own memory (low confidence). The `name-corners` skill drives this.

## 7. Analysis

The deterministic layer that makes the agent cheap and reliable: the CLI computes the numbers,
the model chooses what matters and how to say it. Small or large, the model never has to derive
a brake point from raw samples to be useful.

- **Corner map** (`iagent/analysis/corners.py`): a corner is a stretch of sustained lateral g in
  the median profile of representative laps (smoothed over ~30 m; on above 45% and off below 20%
  of the car's 98th-percentile lateral g, so it adapts to the car). A direction change splits a
  corner (chicanes); same-direction parts closer than 60 m merge (double apexes). The apex is the
  slowest point, or the peak lateral g for corners taken flat. The map also tiles the lap into
  one **segment** per corner, bounded by the last top-speed point before each braking zone, so
  per-corner time deltas sum exactly to the lap delta. On real Spa laps this finds 16 corners
  that line up with the circuit's named corners; Okayama, Watkins Glen and Silverstone look
  plausible.
- **Per-corner metrics**: segment time, brake onset (searched from 50 m before the segment, since
  braking starts just before the speed peak), entry/min/exit speed, full-throttle point (held
  20 m), minimum gear, and metres off track (so an incident is visible as an incident).
- **Comparison and consistency**: corner-by-corner differences against a reference lap, and the
  spread (std dev) of each metric across representative laps, the main track-learning measure.
- Also: distance splits, section comparison, channel traces.
- **Next (phase 4)**: the same metric code runs live at each corner exit, so analysis, rules and
  backtests agree.
- References: own best, theoretical best (best corner segments stitched), imported laps
  (`.ibt`/CSV dropped into the workspace). Garage61 remains a possible source (a v1 client is in git history); whether its API
  allows laps from outside the user's team is unverified.

## 8. Rules, schedules and events

Created by the agent via the CLI; evaluated by the service. A rule is data, not code.

```json
{
  "id": "spa-bus-stop-brake-feedback",
  "track": "spa-2024-up",
  "when": {"corner_exit": "Bus Stop"},
  "if": "brake_m < target_brake_m - 10",
  "action": {"say": "Bus Stop: braked {early_m:.0f} metres early.", "priority": "feedback"},
  "limits": {"cooldown_laps": 1}
}
```

- **Triggers**: a track position with a lead time (`target − speed × lead_seconds`, so a cue ends
  before the corner), a corner exit carrying that corner's metrics, lap complete, pit entry/exit,
  or a schedule.
- **Predicates**: a small restricted expression language over current channels and corner
  metrics. No arbitrary code in the 60 Hz loop.
- **Actions**: `say` (spoken directly by the service, no model), `wake` (send the event to the
  agent), `log`.
- **Backtest before activation**: `iagent rules backtest` runs the *same evaluator* over stored
  laps and reports where it would have fired. Replay is fast enough that no separate engine is
  needed.
- Edge-triggered with hysteresis, cooldowns and global rate limits.

## 9. Voice and UI

- **Input:** push-to-talk → STT on the PC CPU (faster-whisper `small` int8, Parakeet or
  Moonshine) → utterance event → agent.
- **Output:** a speech arbiter owns the audio device: priority, expiry (a late corner cue is
  dropped), no interrupting higher priority, minimum quiet gap. Kokoro TTS on CPU; rule cues are
  pre-rendered when rules are activated.
- **CrewChief coexistence:** no integration; sparse speech, a different voice, avoid spotter/fuel
  topics.
- **CPU cost on the sim PC is unmeasured**; checked in the live phase before committing.
- **UI:** a local web app served by the service, viewable from any machine on the LAN: laps,
  corner comparisons, traces, rules, and what the agent did and said.

## 10. Test rig

Only the telemetry source and the audio ends touch the sim or hardware.

| Seam | Real | Test |
| --- | --- | --- |
| `TelemetrySource` | live irsdk | `.ibt` replay, synthetic generator |
| Agent wake-up | `claude -p` | recorded command lines (assert what the agent would be told) |
| Audio out | speakers | captured (time, text, priority) |
| STT | push-to-talk | injected utterance text |

Layers:

1. **Unit**: segmentation, resampling, store, analysis, rule evaluation, arbiter.
2. **Real-file regression** (`tests/real/`, skipped without the files): lap times match the sim's
   own `LapLastLapTime`; representative laps per file are pinned.
3. **CLI contract**: every agent-facing command's `--json` shape.
4. **Skill evals**: scenario workspaces + a prompt, run headless through the harness with each
   candidate model; programmatic checks (right commands run, numbers in the answer appear in CLI
   output, answer length). This is the gate for using a local model.
5. **Live smoke test** (manual, sim running).

Test data: synthetic laps with exact ground truth (committed); real `.ibt` recordings in
`data/telemetry/` (git-ignored): Okayama, Spa, Silverstone (F4) and Watkins Glen (GT3).

## 11. Package layout

```text
iagent/
  telemetry/   frames, session info, sources (ibt; irsdk live later)
  laps/        segmenter, resampling, store, pace, recorder
  analysis/    corner map and metrics, landmarks, splits, comparison
  testing/     synthetic generator, .ibt writer
  cli.py       the `iagent` command
```

## 12. Phasing

1. **Foundations** (done): sources, segmentation, store, pace filter.
2. **CLI + skills slice** (done): agent-facing CLI, `coach` plugin with `telemetry` and
   `lap-review` skills, verified headless with `claude -p` on real recordings.
3. **Analysis** (done): corner map, per-corner metrics, comparison and consistency, CrewChief
   landmark hints, `name-corners` skill. Verified headless: the agent named Spa's corners and
   reviewed the session corner by corner.
4. **Live**: irsdk source, service, rules/schedules/backtest, `say`, wake-up adapter, TTS.
   *Exit: corner-exit feedback spoken on the real rig; frame-time impact measured.*
5. **UI**: web app over the store, analysis and agent activity.
6. **Voice in + learning loop**: push-to-talk STT, debrief and focus skills, memory across
   sessions, skill evals with local models.

## 13. Open questions and risks

- Local models in a coding-agent harness on this hardware; mitigated by deterministic analysis,
  short skills and the eval gate, with Claude via subscription as the primary.
- Subscription usage limits under frequent wake-ups; mitigated by per-lap/event cadence.
- Corner maps from very few laps (Silverstone has one) are noisier; re-map as laps accumulate.
- Corner maps are derived from one car's laps; a much faster car may take some corners flat that
  another brakes for. Geometry is shared per track for now.
- STT/TTS CPU impact on iRacing frame times: unmeasured.
- Garage61 external reference laps: unverified; own best and imports cover the need.
