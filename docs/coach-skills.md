# The coach agent and its skills

Between sessions the coach is an agent in an existing harness, with skills (markdown) that call
the `iagent` CLI. Skills in [coach/skills/](../coach/skills/):

| Skill | What it does |
| --- | --- |
| `telemetry` | How to find, inspect and compare laps and corners with the CLI, and how to read the numbers |
| `lap-review` | Reviews a session corner by corner, finds the most *repeatable* time loss, gives one focus and writes it to the notes |
| `name-corners` | Names the derived corners (driver, CrewChief, web, own knowledge, with confidence) and records track knowledge |
| `reference-laps` | Picks a faster Garage61 lap (yours or a teammate's), coaches from the corner-by-corner difference, and installs its ghost for iRacing |
| `live-rules` | Writes rules for the live coach (what to watch for on track and what to say), backtests them and activates them |

## Claude Code

```bash
claude plugin marketplace add /path/to/iracing-agent    # or <github-owner>/<repo>
claude plugin install coach@iracing-agent
```

Or, while developing, load it for one session: `claude --plugin-dir coach`. Then ask *"How did my
last Spa session go?"*. The coach names the track's corners if needed (`name-corners`), compares
your laps corner by corner (`lap-review`) and writes its focus to `workspace/notes/<track>.md`.

Headless:

```bash
claude -p "How did my last session go?" --plugin-dir coach \
  --allowedTools "Bash(iagent *)" Read Write Edit --permission-mode acceptEdits
```

**Local models:** point Claude Code at Ollama (v0.14+) with
`ANTHROPIC_BASE_URL=http://<host>:11434 ANTHROPIC_AUTH_TOKEN=ollama claude --model <model>`.
Expect small models (what fits on a 16 GB Mac) to be much less reliable at multi-step tool use.

## Other harnesses

The skills are plain `SKILL.md` folders that only call the `iagent` CLI. Point any harness that
reads Agent Skills (Codex, OpenCode, Goose, ...) at `coach/skills/`.
