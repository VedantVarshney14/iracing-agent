# iRacing Agent — AI-Powered Sim-Racing Coach

An event-driven, multi-agent coaching system for iRacing that provides real-time feedback via driver radio. Built with LangGraph, MCP (Model Context Protocol), and local LLMs via Ollama.

## Overview

iRacing Agent acts as a virtual race engineer and coaching team monitoring your driving in real-time. It detects on-track incidents (brake lock-ups, and more via extensible plugins), analyses your telemetry, and communicates actionable feedback over a text-to-speech radio channel — just like a real pit wall.

## Architecture

```text
┌───────────────────────────────────────────────────────────┐
│                       event_agent.py                      │
│                                                           │
│   ┌──────────────┐  ┌──────────────┐  ┌───────────────┐   │
│   │ Driver Input │  │    Event     │  │  Main Async   │   │
│   │   Thread     │  │   Listener   │  │     Loop      │   │
│   │ (stdin msgs) │  │  Thread      │  │               │   │
│   └──────┬───────┘  └──────┬───────┘  └──────┬────────┘   │
│          │                 │                 │            │
│          └─────────────────┴─────────────────┘            │
│                     ThreadPriorityQueue                   │
└───────────────────────────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────┐
│                     LangGraph Agent                       │
│                                                           │
│  START ──► trigger_check                                  │
│                   │                                       │
│         ┌─────────┴──────────┐                            │
│         ▼                    ▼                            │
│   race_engineer ◄────── assistant_coach                   │
│   (driver msgs)  handoff (session events)                 │
│      │  ▲                    │  ▲                         │
│      ▼  │                    ▼  │                         │
│   re_tools                ac_tools                        │
│         │                                                 │
│         ▼                                                 │
│        END                                                │
│         │                                                 │
│         ▼                                                 │
│   TTS Radio Output                                        │
└───────────────────────────────────────────────────────────┘
```

## Key Features

- **Real-time event detection** — Polls iRacing telemetry at 20 FPS; detector plugins fire when incidents are detected (brake lock-ups implemented, extensible to more)
- **Two-agent coaching pipeline** — An Assistant Coach analyses raw events and hands structured reports to a Race Engineer, who decides what to relay to the driver
- **Driver radio** — Responses are spoken via a local TTS model, simulating real pit-wall radio
- **Lap analysis** — Integrates with [Garage61](https://garage61.net) to pull lap data.
- **Live telemetry tools** — Agents can query 300+ iRacing SDK variables in real-time via MCP tool calls
- **Priority queue** — Driver messages always pre-empt background coaching events
- **Fully local** — Runs entirely on-device using Ollama LLMs; no cloud API keys required

## Tech Stack

| Layer | Technology |
| --- | --- |
| Agent orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) |
| LLM inference | [Ollama](https://ollama.ai) (`qwen3:8b`) |
| Tool integration | [MCP](https://modelcontextprotocol.io) via `fastmcp` + `langchain-mcp-adapters` |
| iRacing SDK | `pyirsdk` |
| Lap telemetry | Garage61 REST API |
| Text-to-speech | Kokoro TTS (`hexgrad/Kokoro-82M`) |
| Telemetry storage | PostgreSQL via SQLAlchemy (optional) |
| Semantic search | `sentence-transformers` (`all-MiniLM-L6-v2`) |

## Prerequisites

- Python 3.12+
- [Ollama](https://ollama.ai) with `gemma4:e4b` pulled: `ollama pull gemma4:e4b`
- iRacing running on the same machine (not required in debug mode)
- (Optional) [Garage61](https://garage61.net) account + personal access token for lap analysis
- (Optional) PostgreSQL instance for telemetry logging

## Installation

```bash
git clone https://github.com/VedantVarshney14/iracing-agent
cd iracing-agent
uv sync
```

## Configuration

Environment variables (can also be supplied via a `.env` file passed to `--env`):

| Variable | Required | Description |
| --- | --- | --- |
| `TEXT_MODEL` | No | Ollama model name to use (default: `gemma4:e4b`) |
| `DRIVER_NAME` | No | Driver's name, used by the agents when addressing the driver |
| `GARAGE61_PAT` | No | Garage61 personal access token for lap analysis tools |
| `NO_TTS` | No | Set to `1` to disable text-to-speech output |
| `DEBUG` | No | Set to `1` to use a recorded telemetry snapshot instead of live iRacing (also set by `--debug`) |

### Testing locally (debug mode)

To test without iRacing running, create a `.env` file:

```dotenv
NO_TTS=TRUE
DEBUG=TRUE
GARAGE61_PAT=<your_garage61_pat>
TEXT_MODEL=gemma4:e4b
DRIVER_NAME=<your_name>
```

Debug mode expects a `data/` directory in the current working directory containing:

- `data.bin` — iRacing shared memory binary snapshot (used in place of live iRacing data)

## Usage

The MCP server must be running before you start the agent. Start it in a separate terminal:

```bash
# Start the composite MCP server (iRacing must be running)
uv run composite-mcp

# Pass a Garage61 personal access token directly (alternative to setting GARAGE61_PAT env var)
uv run composite-mcp <GARAGE61_PAT>

# Override the default port (8000)
MCP_PORT=9000 uv run composite-mcp
```

Then, in another terminal, start the agent:

```bash
# Start the agent
uv run iracing-agent

# Load environment variables from a .env file
uv run iracing-agent --env .env

# Enable debug mode — uses recorded telemetry, no live iRacing needed
uv run iracing-agent --debug

# Disable TTS
NO_TTS=1 uv run iracing-agent

# Supply a phonetics file for TTS pronunciation overrides
uv run iracing-agent --phonetics phonetics.yaml
```

**Example session:**

```shell
$ uv run iracing-agent --env .env
[16:57:40] [INFO] Skipping TTS
[16:57:40] [INFO] Setting up Event Listener and unified priority queue
[16:57:40] [INFO] Setting up tools
Driver message ([Enter] to use the default): Hey!
Driver Message received: Hey!
AI Message: Hey Vedant! Good to hear from you. Let me know if you need anything.
Driver message ([Enter] to use the default): How was my last lap?
Driver Message received: How was my last lap?
AI Message: Vedant - Thanks. Focus on smoother throttle input and managing the slide through Turns 1 and 2.
Driver message ([Enter] to use the default): What was my last lap time?
Driver Message received: What was my last lap time?
AI Message: Vedant - Your last lap time was 1:24.894. Keep an eye on balancing the times between sectors 2 and 3 for better consistency.
Driver message ([Enter] to use the default):
```

The phonetics file is a YAML dict mapping words to their phonetic strings, e.g.:

```yaml
Vedant: "/ˈvɪdænt/"
Spa: "/spɑː/"
```

Once running, press **Enter** at any prompt to send a message via the driver radio. The Race Engineer agent will respond and speak the reply aloud (unless `NO_TTS=1`).

## How It Works

1. **Event listener** polls iRacing telemetry at 20 FPS. Detector plugins (e.g. `LockUpDetector`) emit `EventStamp` objects when incidents are detected.
2. **Driver input thread** reads from stdin; each message is wrapped as a high-priority trigger.
3. **Priority queue** merges both streams — driver messages (priority 0) always pre-empt background coaching events.
4. **Trigger processor** routes each trigger through the LangGraph agent:
   - *Driver message* → Race Engineer node directly (can call MCP tools, then replies to driver and/or coach)
   - *Session event* → Assistant Coach node first (analyses the batch, hands structured report to Race Engineer)
5. **Race Engineer** decides what to relay to the driver. If a driver-facing message is produced, it is spoken via TTS.

## Tests

> **Note:** The test suite is designed for local development and manual verification — not CI/CD pipelines. Tests require a real iRacing telemetry snapshot to run.
>
> Place a `.bin` telemetry file (recorded from an iRacing session) at `tests/data/iracing-telemetry.bin` before running `pytest`.

## Project Structure

```text
iracing-agent/
├── iagent/
│   ├── agent.py                      # LangGraph agent (Race Engineer + Assistant Coach)
│   ├── models.py                     # IRSDKVars dataclass (300+ telemetry fields)
│   ├── tools.py                      # iRacing telemetry lookup + semantic search
│   ├── telemetry.py                  # Background telemetry collection to PostgreSQL
│   ├── vision.py                     # Vision model wrapper for chart analysis
│   ├── priority_queue.py             # Thread-safe priority queue
│   ├── audio/
│   │   └── tts.py                    # Kokoro TTS for radio output
│   ├── events/
│   │   ├── event_listener.py         # 20 FPS polling loop
│   │   ├── events.py                 # Event types and EventStamp dataclass
│   │   └── detectors/
│   │       ├── base.py               # Abstract detector interface
│   │       └── lockup.py             # Brake lock-up heuristic detector
│   ├── garage/
│   │   ├── garage_client.py          # Garage61 async REST client
│   │   ├── models.py                 # Lap Pydantic model
│   │   └── plot.py                   # Telemetry visualisation (speed, braking, etc.)
│   ├── db/                           # PostgreSQL telemetry storage (optional)
│   ├── mcp_servers/
│   │   ├── iracing_mcp.py            # Live telemetry MCP server
│   │   ├── garage_mcp.py             # Lap analysis MCP server
│   │   └── composite_mcp.py          # Combined MCP server (entry: composite-mcp)
│   ├── scripts/
│   │   └── event_agent.py            # Agent entry point (entry: iracing-agent)
│   └── serialize/
│       └── json.py                   # Dataclass-aware JSON encoder
├── tests/                            # Test suite
└── data/                             # Track corner reference data and phonetics
```

## Extending the Agent

### Adding a new event detector

Subclass `iagent.events.detectors.base.BaseDetector`, implement `detect(telemetry) -> EventStamp | None`, and register it in `EventListener`.

### Adding MCP tools

Add tools to `iagent/mcp_servers/iracing_mcp.py` or `garage_mcp.py`, or create a new FastMCP server and mount it in `event_agent.py`.

## License

All rights reserved.
(I may change my mind later. If you are keen to contribute - let me know!)
