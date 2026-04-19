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
│          ┌────────┴────────┐                              │
│          ▼                 ▼                              │
│    race_engineer     assistant_coach                      │
│    (driver msgs)     (session events)                     │
│          │                 │                              │
│          ▼                 ▼                              │
│      re_tools          ac_tools                           │
│          │                 │                              │
│          │    (coach → engineer handoff)                  │
│          ▼                 │                              │
│         END ◄──────────────┘                              │
│          │                                                │
│          ▼                                                │
│     TTS Radio Output                                      │
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
- [Ollama](https://ollama.ai) with `qwen3:8b` or similar pulled: `ollama pull qwen3:8b`
- iRacing running on the same machine
- (Optional) [Garage61](https://garage61.net) account + personal access token for lap analysis
- (Optional) PostgreSQL instance for telemetry logging

## Installation

```bash
git clone https://github.com/VedantVarshney14/iracing-agent
cd iracing-agent
uv sync
```

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `GARAGE61_PAT` | No | Garage61 personal access token for lap analysis tools |
| `NO_TTS` | No | Set to `1` to disable text-to-speech output |
| `DEBUG` | No | Set to `1` to use a recorded test telemetry snapshot instead of live iRacing |
| `DB_USERNAME` | No | PostgreSQL username for background telemetry logging |
| `DB_PASSWORD` | No | PostgreSQL password for background telemetry logging |

## Usage

```bash
# Start the agent (iRacing must be running)
python event_agent.py

# Disable TTS — useful for development
NO_TTS=1 python event_agent.py

# Run against test data without iRacing open
DEBUG=1 NO_TTS=1 python event_agent.py
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

## Project Structure

```text
iracing-agent/
├── event_agent.py                    # Entry point — multi-threaded event loop
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
│   │   └── composite_mcp.py          # Combined MCP mount
│   └── serialize/
│       └── json.py                   # Dataclass-aware JSON encoder
├── scripts/
│   └── corner_mapper.py              # Interactive track corner mapping utility
├── tests/                            # Test suite
└── track-data/                       # Track corner reference data (JSON)
```

## Extending the Agent

### Adding a new event detector

Subclass `iagent.events.detectors.base.BaseDetector`, implement `detect(telemetry) -> EventStamp | None`, and register it in `EventListener`.

### Adding MCP tools

Add tools to `iagent/mcp_servers/iracing_mcp.py` or `garage_mcp.py`, or create a new FastMCP server and mount it in `event_agent.py`.