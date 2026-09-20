# iRacing Agent — a local AI coach for learning tracks

A local, model-agnostic coach for iRacing. The first goal is helping a driver **learn a new
track**: derive the corners from recorded laps, compare each one against a reference, pick one
thing to work on, and cue it by voice at the right place on track. Race craft and strategy come
later on the same foundations.

> **Status: rewrite in progress (branch `rewrite/v2`).** Phase 1 of 6 is done. The earlier
> two-agent LangGraph prototype has been removed; see the git history on `main` if you need it.

The design and rationale live in [.claude/architecture.md](.claude/architecture.md). In short:

- **One coach** with a sandboxed workspace (recorded laps, notes, `run_python`).
- **Agent-defined events:** the agent writes rules, backtests them on recorded laps, and a
  deterministic runtime fires them. No LLM in the real-time loop.
- **Split deployment:** a light edge runtime on the sim PC (telemetry, rules, voice) and the
  "brain" (agent + model) on another machine. Both can run on one machine.
- **Swappable model** behind an OpenAI-compatible endpoint, with tools exposed over MCP. No
  per-token API costs required.
- **Testable without the sim:** recorded `.ibt` replay, a synthetic lap generator with exact
  ground truth, and (later) a model eval suite.

## Roadmap

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Telemetry sources (`.ibt` replay, synthetic), lap segmentation, lap store | **Done** |
| 2 | Corner-map derivation, per-corner metrics, reference laps, `run_python` sandbox | Next |
| 3 | Agent loop, model adapter and capability profiles, MCP tool surface, eval suite | |
| 4 | Agent-defined rules: schema, edge evaluator, backtesting | |
| 5 | PC ↔ brain split, speech arbiter, TTS with cue cache, push-to-talk STT | |
| 6 | The track-learning loop: focus, debrief, memory across sessions | |

## What works today

Replay a telemetry source through lap segmentation into a lap store:

```bash
uv sync

# Generated laps with known ground truth (--messy adds off-track, pit and reset laps)
uv run iagent replay synthetic --laps 6 --messy --store workspace

# A recorded iRacing session
uv run iagent replay path/to/session.ibt --store workspace

# List what was stored
uv run iagent laps --store workspace --valid-only
```

```text
lap                               time  valid  reasons
synthetic-0-L000                     -  False  incomplete
synthetic-0-L001                58.317  False  off_track
synthetic-0-L002                58.223  False  pit_road
synthetic-0-L003                58.393  False  pit_road
synthetic-0-L004                84.750  False  discontinuity
synthetic-0-L005                58.135  True
```

- Lap times are interpolated across the start/finish crossing, so they are sub-frame accurate.
- Flagged laps are stored, not discarded, so the coach can decide what to use. The first lap of a
  session is always `incomplete` because its start/finish crossing was never observed.
- Each lap is stored raw (60 Hz) and resampled onto a 1 m distance grid, so laps compare point
  for point.

iRacing can record `.ibt` files itself (default hotkey Alt+L, saved under
`Documents/iRacing/telemetry`).

## Layout

```text
iagent/
  common/     frames, channel definitions, session info
  edge/       telemetry sources (the part that runs on the sim PC): .ibt replay
  brain/      lap segmentation, distance resampling, lap store, recorder
  testing/    synthetic lap generator, .ibt writer
  audio/      Kokoro TTS wrapper (to be reworked in phase 5)
  garage/     Garage61 client (to become a reference-lap provider)
  cli.py      `iagent` command
tests/        pytest suite (runs without the sim or a GPU)
.claude/
  architecture.md   target architecture, protocol and phasing
```

## Tests

```bash
uv run pytest
```

The suite needs no sim, no model and no network. `tests/test_tts.py` is a leftover manual
debugging test that is always skipped; it goes when the TTS wrapper is reworked. Real `.ibt` recordings, once added, are kept out of git (see
`.gitignore`) and will drive additional regression tests.

## Requirements

Python 3.12+ and [uv](https://docs.astral.sh/uv/). iRacing itself is only needed for the live
telemetry source, which arrives in phase 5. A [Garage61](https://garage61.net) token
(`GARAGE61_PAT`) is optional and only used for reference laps.

## License

All rights reserved.
(I may change my mind later. If you are keen to contribute - let me know!)
