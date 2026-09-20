# Architecture (v2 draft)

Status: **draft for discussion — nothing here is implemented yet.** It supersedes the
two-agent LangGraph design described in the README.

## 1. Goals

**Primary problem: help the driver learn a new track.** First test bed: Spa-Francorchamps.
Race-craft tips, strategy and fuel come later and should reuse the same foundations.

Design goals:

1. **One coach**, one conversation, with a flexible workspace instead of a small fixed tool set.
2. **The agent defines its own events** (rules). The harness provides the framework to
   evaluate them reliably; it does not hardcode what matters.
3. **The model is swappable.** No component outside the model adapter knows which model runs.
4. **Fully testable without the sim** (section 11).
5. **Local by default.** No per-token API cost. A hosted agent may be plugged in for debriefs,
   but nothing depends on one.

Non-goals (for now): replacing CrewChief's spotter / fuel / gap calls, VR-specific UI,
multi-driver or team-radio scenarios, lock-up detection (may return later as an
agent-defined rule).

## 2. What "learning a track" means

The loop the system supports:

1. **Map** — derive the corner map from recorded laps (speed minima, steering, distance).
   No hand-built per-track JSON.
2. **Compare** — per corner, compare the driver to a reference: brake point, minimum speed,
   throttle pickup point, line/steering, and *consistency* across recent laps.
3. **Focus** — pick one thing at a time ("this session: T5 brake point, 20 m later").
4. **Cue** — deliver the focus at the right place on track with a short spoken cue.
5. **Debrief** — between laps / after the session, analyse and update the plan.
6. **Remember** — persist progress and the driver's own notes across sessions.

Success measures: lap-time delta to reference; per-corner brake-point spread (std dev in
metres) shrinking over a session; time until N corners are within tolerance.

## 3. System overview

```text
        PC (Windows, runs the sim)                     Mac (brain)
┌───────────────────────────────────┐        ┌──────────────────────────────────┐
│ Edge runtime                      │        │ Coach service                    │
│  ├─ Telemetry source (irsdk)      │ frames │  ├─ Recorder → lap store         │
│  ├─ Rule engine (cues, events)    │───────►│  ├─ Workspace (laps, notes)      │
│  ├─ Push-to-talk + STT (CPU)      │  text  │  ├─ Sandbox (run_python)         │
│  ├─ Speech arbiter + TTS (CPU)    │───────►│  ├─ Agent loop  ──► Model adapter│──► LLM endpoint
│  └─ Ring buffer / spool           │◄───────│  ├─ Rule authoring + backtest    │
└───────────────────────────────────┘ rules, │  └─ Tool surface (MCP)           │◄── external agent
                                      say()  └──────────────────────────────────┘    (optional)
```

Why split this way:

- iRacing shared memory is Windows-only, so something must run on the PC.
- The 3070 is needed by the sim; LLM inference lives on the Mac (or any machine we point it at).
- Rules run **next to the data**, so a cue fires with no network hop and no LLM in the path.
  If the Mac sleeps or Wi-Fi drops, planned cues keep firing.
- The edge runtime stays dumb and cheap; all intelligence is on the brain side.

The two halves are also runnable on one machine (loopback), which is what tests and a
single-PC setup use.

## 4. Data model

### Frames

The unit of telemetry: a timestamped dict of channels. Sampling is 60 Hz on the PC; the stream
sends a configurable subset (default ~30 channels: `SessionTime, LapDistPct, LapDist, Speed,
Throttle, Brake, Clutch, SteeringWheelAngle, Gear, RPM, LatAccel, LongAccel, VertAccel, Lap,
OnPitRoad, IsOnTrack, ...`). The agent can request additional channels per session.
Time comes from `SessionTime`, never wall clock (this is what makes replay possible).

### Lap store (Mac)

- One file per lap (Parquet by default), holding the raw time series plus a version resampled onto a
  **fixed distance grid** (1 m steps from `LapDist`) so laps are directly comparable. Parquet is
  chosen for exact dtypes and ~5–10× smaller files as laps accumulate across sessions, not because
  the data is large (a Spa lap is ~8k rows). Access goes through a `LapStore` interface, so the
  format is an implementation detail and CSV would be a drop-in alternative. Small derived
  artifacts (corner map, notes, reference summaries) stay JSON/markdown.
- Index (SQLite or a single Parquet): `lap_id, track, car, session, lap_time, valid, sectors,
  conditions, source (live|ibt|garage61|import)`.
- Derived artifacts (corner map, reference "theoretical best") are stored as files in the
  workspace, not in a database, so the agent can read and rewrite them.

### Workspace

```text
workspace/
  laps/<track>/<car>/<lap_id>.parquet      # read-only to the sandbox
  reference/<track>/<car>/...              # reference laps (any source)
  tracks/<track>/corners.json              # derived geometry + aligned names, agent-editable
  tracks/<track>/knowledge.md              # researched-once track knowledge (names, quirks)
  notes/
    driver.md                              # who the driver is, preferences, goals
    <track>.md                             # per-track learnings, current focus
    sessions/<date>.md                     # debrief summaries
  rules/<track>/*.json                     # active + archived rules
  scratch/                                 # sandbox-writable
```

Memory is plain markdown the agent reads at session start and edits at debrief. No special
memory API.

### Track knowledge (corner names and quirks)

Corner *geometry* and corner *knowledge* are separate layers:

1. **Geometry (derived):** where corners are, from speed minima, steering and distance, stored as
   distance ranges in `corners.json`.
2. **Knowledge (researched):** names and character — e.g. Spa's Eau Rouge/Raidillon compression,
   Pouhon double-apex, Blanchimont flat-out — stored in `knowledge.md`.

A small local model will half-remember famous circuits and invent details with confidence, so its
own memory is one input, not the source of truth. `knowledge.md` is **researched once and cached**
from these sources, in rough order of trust:

- **CrewChief track landmarks** (`trackLandmarksData.json`: name, `distanceRoundLapStart`,
  `distanceRoundLapEnd`, common overtaking spot; MIT-licensed, reportedly includes Spa). Used as
  *data we import*, not a runtime integration. Coverage and accuracy unverified (spike, section 13).
- **Web search** as a debrief-mode tool (any agent with search; results summarised into the file,
  with sources noted).
- The model's own knowledge, flagged as unverified.

Alignment: the agent matches named corners to derived corners by order and approximate lap
distance, writes the result into `corners.json`, and records its confidence. The driver can
correct it, and corrections persist. iRacing's session info gives sector splits but no corner
names, so names always come from the sources above.

## 5. PC ↔ brain protocol

WebSocket, JSON messages (msgpack later if needed). Every message has `type`, `seq`, `t`
(session time). Sequence numbers let the PC resend after a reconnect from its ring buffer.

| Direction | Message | Purpose |
| --- | --- | --- |
| PC → brain | `hello` | edge version, sim/track/car, available channels |
| PC → brain | `frames` | batch of frames (e.g. 10 per message) |
| PC → brain | `lap_complete` | lap boundary, validity flag, lap time |
| PC → brain | `rule_fired` | rule id, session time, lap distance, captured context |
| PC → brain | `utterance` | STT text from push-to-talk |
| brain → PC | `set_rules` | full rule set (replace) |
| brain → PC | `say` | text, priority, optional `earliest`/`latest` window, cache key |
| brain → PC | `set_channels` | change the streamed channel subset |

`say` carries a **priority** and an **expiry**: a corner-entry cue that arrives 4 s late is
worse than silence, so the arbiter drops expired speech.

## 6. Rules (agent-defined events)

A rule is data, not code. The agent authors rules; the edge runtime evaluates them each frame.

```json
{
  "id": "spa-t5-brake-marker",
  "track": "spa",
  "when": {
    "distance": {"pct": 0.412, "lead_seconds": 2.0},   // fire early enough to speak
    "if": "Speed > 180 and Brake < 0.05"                // optional predicate
  },
  "action": {"say": "Brake board. Twenty metres later than last lap.", "priority": "cue"},
  "limits": {"cooldown_laps": 1, "max_per_lap": 1, "hysteresis": 0.02},
  "report": ["Speed", "Brake", "Throttle"]
}
```

Semantics:

- **Predicates** use a restricted expression language over current channels plus a small set of
  derived channels and rolling windows (`max(Brake, 0.5s)`, `delta(Speed, 0.2s)`). No arbitrary
  code executes in the 60 Hz loop. (Candidate implementation: a sandboxed expression library or
  CEL; decision deferred to the spike.)
- **Distance triggers** use a *lead time*: the trigger point is `target − speed × lead_seconds`,
  so the cue finishes before the corner regardless of speed.
- **Actions:** `say` (spoken), `notify_agent` (wake the coach with a context window of the last
  N seconds), `log` (record only).
- **Edge-triggered** with hysteresis and cooldowns; per-rule and global rate limits stop a
  buggy rule from flooding the driver.

**Backtesting is mandatory before activation.** `backtest_rule(rule, laps)` replays a rule
against stored laps and returns where and how often it would have fired. The agent reads that,
adjusts thresholds, and only then calls `activate_rule`. This is the mechanism that replaces
hand-tuned detectors: the agent calibrates against real data.

## 7. The agent

### Loop

A plain async loop — no graph framework:

```text
wait for trigger (utterance | rule_fired notify | lap_complete | schedule | manual)
  → build context (system prompt + notes + recent state + trigger)
  → model turn(s) with tools until a final answer / no more tool calls
  → outputs: say() calls, note writes, rule changes
```

### Modes

Same agent, different budgets — not different agents.

| Mode | Trigger | Latency budget | Behaviour |
| --- | --- | --- | --- |
| Live | utterance, `notify_agent`, lap boundary | seconds | short context, few tool calls, brief speech |
| Debrief | pit lane / session end / manual | minutes | full analysis, corner map, plan, rules, notes |

Most in-lap speech should be **precomputed cues** from debrief, not LLM output.

### Tool surface (exposed over MCP)

The same functions serve the built-in loop (called in-process) and any external agent (over MCP).

| Tool | Purpose |
| --- | --- |
| `list_laps(filter)` / `get_lap(id)` | browse the lap store |
| `run_python(code)` | sandboxed pandas/numpy over the workspace (below) |
| `get_reference(track, car, kind)` | fetch a reference lap (section 9) |
| `define_rule` / `backtest_rule` / `activate_rule` / `list_rules` / `retire_rule` | rule lifecycle |
| `say(text, priority, expires)` | radio output |
| `read_notes` / `write_notes` | memory (files) |
| `live_snapshot(channels)` | current values, for quick checks |

Deliberately few. Anything analytical goes through `run_python`, because small models write
short pandas more reliably than they compose many narrow tools.

### Sandbox

- Subprocess per call, timeout, memory cap, CPU niceness, **no network**.
- `laps/` and `reference/` mounted read-only; `scratch/` writable.
- Preloaded helpers (`load_lap`, `resample_distance`, `corner_metrics`, `plot`) so the model
  writes little code. Output truncated and structured to protect context.
- Candidate implementation: a plain subprocess with `resource` limits first; container/WASM only
  if needed. Threat model is "model makes mistakes", not "model is adversarial", but treat
  any external MCP client as untrusted.

## 8. Model seam

Two existing standards, no bespoke protocol:

- **Harness → model:** OpenAI-compatible Chat Completions with `tools` (Ollama, `llama-server`,
  LM Studio, vLLM, `mlx-lm`). Config is `base_url`, `model`, and a capability profile.
- **Any agent → coach:** MCP over the tool surface above.

"OpenAI-compatible" is only approximately compatible, so a thin **adapter layer** absorbs the
differences, one small module per quirk, enabled by the model's capability profile:

- tool calls emitted as XML / in content instead of `tool_calls` (seen with some Qwen builds);
- reasoning / thinking tokens leaking into `content`, or a separate `reasoning` field;
- malformed or truncated JSON arguments → repair once, then re-prompt;
- servers that reject `tools` + `response_format` together;
- system-prompt handling and role-alternation differences; max-token and stop-sequence defaults.

A capability profile per model (`supports_tools`, `parallel_tools`, `thinking: on|off|field`,
`context`, sampling defaults) lives in config, not code. **A model is only trusted after it
passes the eval suite** (section 11); swapping models means editing config and re-running it.

Baseline candidates to evaluate first: Gemma 4 E4B, Qwen3-4B / Qwen3.5-4B (Mac, 16 GB),
Gemma 4 12B for debrief. Treat published benchmarks as hints only.

## 9. Reference laps

Behind one interface, so the source can change:

```text
ReferenceProvider.get(track, car, kind) -> Lap
  kinds: own_best | theoretical_best | external
```

Sources:

1. **Own best** and **theoretical best** (best corner-by-corner segments stitched from the
   driver's own laps) — always available, no external dependency. Start here.
2. **Garage61** — the existing client already fetches a lap's CSV. **Unverified:** whether the API
   lets us fetch laps from drivers outside the user's team, and which plan tier is needed for
   telemetry. Both the developer portal and endpoint pages are JavaScript-rendered and could not be read
   offline; to be settled by calling the API with the real token (spike, section 13).
3. **Manual import** — CSV or `.ibt` dropped into `reference/` (e.g. a friend's lap or a
   coaching-video export). Guarantees the feature works even if Garage61 does not allow it.

Reference laps are normalised to the same distance grid as the driver's laps, and the source
and quality are recorded so the agent can say how much to trust a comparison.

## 10. Voice

- **Input:** push-to-talk (wheel button/hotkey) → mic capture → STT on the PC CPU → text →
  `utterance`. Candidates: faster-whisper `small` (int8), Parakeet, Moonshine. Runs only while
  talking, capped to 2 threads.
- **Output:** the **speech arbiter** on the PC owns the audio device. Inputs: `say` messages and
  local rule actions, each with priority and expiry. It queues, drops expired items, never
  interrupts a higher-priority item, and enforces a minimum quiet gap.
- **TTS:** Kokoro (ONNX, CPU). Planned cues are **pre-rendered** when rules are pushed
  (cache key = text + voice), so firing costs nothing. Only ad-hoc replies are synthesised live.
- **CrewChief coexistence:** it has no plugin API, so we don't integrate; we stay out of its way.
  Keep in-lap speech sparse, use a different voice, avoid its spotter/fuel topics, and let the
  arbiter be told to hold during spotter-heavy moments (later: detect via a shared "quiet"
  hotkey/flag or proximity channels).
- Not measured yet: CPU cost of STT/TTS on the sim PC. Check iRacing frame times with both
  active before committing (fallback: run STT/TTS on the Mac and stream audio).

## 11. Test rig

The sim can't be a prerequisite for development. The key is that **only the telemetry source
and audio ends are sim/hardware-specific**; everything else runs on recorded or synthetic input.

### Seams (interfaces with fake implementations)

| Seam | Real | Test |
| --- | --- | --- |
| `TelemetrySource` | live irsdk | `.ibt` replay, synthetic generator, recorded frame log |
| `Clock` | wall clock | driven by frame `SessionTime` (replay at 1×, 10×, or as fast as possible) |
| `ModelClient` | OpenAI-compatible endpoint | scripted fake, record/replay cassettes, real local model |
| `AudioOut` | speakers | capture list of (time, text, priority) |
| `Mic` / STT | push-to-talk | inject `utterance` text at a chosen session time |
| `Reference` | Garage61 / files | fixture laps |

### Layers

1. **Unit** — rule expression evaluation, distance triggers, arbiter, sandbox limits, adapter
   quirks. Fast, deterministic, no model.
2. **Replay integration** — feed a recorded session through the whole pipeline with a *fake
   model*. Assertions on lap segmentation, stored laps, rule firings, and the sequence of
   `say` messages.
3. **Rule backtests as golden tests** — fixed laps with known expected firing positions.
4. **Model eval suite** — scenario files: `state + trigger → acceptable behaviour`. Programmatic
   checks first (right tool called, valid arguments, `say` under N words, no invented numbers
   not present in tool output); optional LLM judge later. Run against each candidate model and
   record a scorecard. This is the gate for swapping models.
5. **Live smoke test** (manual, sim running): a short checklist run before releases.

### Test data

- `.ibt` files: iRacing can log full-session telemetry to disk (default hotkey Alt+L, saved under
  `Documents/iRacing/telemetry`). A handful of Spa laps — including a couple of deliberately
  messy ones (off-tracks, spin, pit in/out) — is the core fixture set. Each file needs a short
  **annotation** (car, what happened, e.g. "spun at Les Combes, lap 4") so tests can assert
  correct results, not just "doesn't crash". Use one car/setup family so laps are comparable.
- No public Spa `.ibt` dataset was found (only parsers such as TRACE.IT / ibt-telemetry, whose
  repos may carry small samples). Regression coverage therefore comes from the synthetic
  generator plus our own recordings.
- The current `tests/data/iracing-telemetry.bin` is a single-frame snapshot and is not enough
  for lap-level testing.
- Fixtures are large and stay out of git (keep the existing ignore rules); a small script
  documents how to record and place them. A **synthetic generator** (parametric lap with known
  corners and braking points) provides small committed fixtures and exact ground truth for the
  corner-mapper tests.

## 12. Repository layout and existing code

Proposed:

```text
iagent/
  edge/          # PC runtime: source, rule engine, arbiter, voice, transport
  brain/         # coach service: recorder, lap store, agent loop, sandbox, MCP server
  model/         # OpenAI-compatible client, adapters, capability profiles
  common/        # wire messages, channel definitions, schemas
  testing/       # fake sources, cassettes, synthetic lap generator, eval runner
docs/
evals/           # scenario files and scorecards
```

Existing code, in short:

- **Drop:** LangGraph agent, two-agent prompts, `LockUpDetector`, the event priority queue, the
  sentence-transformer variable lookup (replaced by a static channel catalogue in the prompt).
- **Keep and adapt:** Garage61 client (→ one reference provider), Kokoro TTS wrapper,
  `vars.json` definitions, `serialize/json.py`, track ID map.
- **Remove:** the stdin driver input and the separate MCP HTTP process requirement.

## 13. Phasing

Each phase is testable on its own and adds value without the later ones.

1. **Foundations** — `TelemetrySource` (live + `.ibt` replay + synthetic), `Clock`, lap
   segmentation, lap store. *Exit: replay a Spa session, get clean laps on the distance grid.*
2. **Analysis** — corner-map derivation, per-corner metrics, own-best / theoretical-best
   reference, `run_python` sandbox. *Exit: a script (no LLM) prints a per-corner comparison.*
3. **Agent + model seam** — loop, adapter layer, capability profiles, tool surface (MCP), eval
   suite v0. *Exit: two local models scored on the eval suite.*
4. **Rules** — rule schema, edge evaluator, backtest, activation. *Exit: agent authors a
   brake-marker rule for one Spa corner and backtests it.*
5. **Split + voice** — edge/brain over WebSocket, arbiter, TTS with cue cache, push-to-talk STT.
   *Exit: end-to-end on the real rig, measured frame-time impact.*
6. **Track-learning loop** — focus selection, debrief flow, notes/memory across sessions.

Spikes to run early (cheap, and they remove the biggest unknowns):

- **Garage61 access:** with the real token, try fetching a non-team, non-own lap; note plan
  requirements for telemetry.
- **CrewChief landmarks:** fetch `trackLandmarksData.json`, check Spa coverage and whether
  distances line up with iRacing's `LapDist` on our recorded laps.
- **Frame-time impact:** run Kokoro + faster-whisper on the PC CPU during a Spa session.
- **Model baseline:** run 2–3 candidate models against a few hand-written eval scenarios.
- **Rule language:** pick and prototype the expression evaluator.

## 14. Open questions and risks

- Garage61 external reference laps (see above); mitigated by own-best and manual import.
- Corner segmentation quality on a track like Spa (long flowing sections, elevation) — needs
  real data before we trust auto-derived corners.
- Small-model reliability on multi-step tool use; mitigated by fewer tools, precomputed cues
  and the eval gate.
- Audio path and hearing the coach on the PC side (edge-runtime TTS resolves this, at the CPU
  cost noted above).
- Where `.ibt`-style logging is off by default: recorder on the Mac already captures the live
  stream, but replay fixtures need one manual logging session.
- Whether a hosted agent (e.g. Claude Code over MCP) is worth wiring for debrief; the design
  allows it but nothing requires it.
