---
name: telemetry
description: Find, inspect and compare the driver's recorded iRacing laps with the `iagent` CLI, corner by corner. Use whenever a question needs lap times, brake points, corner speeds, consistency or traces from real telemetry.
---

# Telemetry with the `iagent` CLI

All lap data comes from the `iagent` command. Run it in a shell, one command at a time. The default
output is a compact table, which is usually all you need; add `--json` when you need exact fields
(every command except `laps trace`, which prints CSV). Don't pipe output through other programs:
the commands' own filters are enough. Never invent numbers: every figure you report must come from
a command's output. If `iagent` is not on the PATH, the driver must install it
(`uv tool install --editable <repo>`); say so and stop.

## Orient first

```sh
iagent workspace             # where the workspace (laps, notes/, tracks/) is
iagent tracks                # track/car combinations with their keys and best times
```

`track` and `car` are **keys** (e.g. `spa-2024-up`, `formulair04`). Use them in filters. Laps on
different keys are never compared (different layout or car).

## Laps

| Command | Gives you |
| --- | --- |
| `iagent laps list --track T [--car C] [--session S] [--representative]` | laps with time, gap to best, off-track seconds, validity |
| `iagent laps trace LAP --from M --to M [--step M] [--channels ...]` | CSV samples along the lap |

## Corners (the main tool)

The corner map is derived from the driver's own laps on first use and saved to
`<workspace>/tracks/<track>/corners.json`. Corners are numbered T1, T2, … in lap order; chicanes
are split into one corner per direction. Names are attached separately (see the `name-corners`
skill); an unnamed corner is just `T7`.

| Command | Gives you |
| --- | --- |
| `iagent corners list --track T` | each corner's direction, entry/apex/exit distance, reference min speed, name |
| `iagent corners report LAP` | per corner: time, brake point, entry/min/exit speed, full-throttle point |
| `iagent corners compare LAP [REF]` | per corner differences vs a reference (default: fastest other valid lap) |
| `iagent corners consistency --track T [--session S]` | per corner spread across representative laps |

Run `iagent <command> --help` for every option.

## Reading the numbers

- **Lap ids** look like `20250723-202727-L005`: session (recording date-time) + lap number.
- **valid** is structural (complete, no pit road, no reset). **representative** = valid and within
  5% of the best lap: use these for coaching. Slower laps are incidents or cool-downs.
- **off_track_s** is information, not a penalty.
- **Corner time** covers the corner's *segment*: from the fastest point before its braking zone to
  the fastest point before the next one, so corner deltas add up to the lap delta. A slow exit
  shows up as time lost in that corner (on the following straight).
- **compare signs**: `delta` > 0 slower; `brake` > 0 braked *later*; `min kph` > 0 carried more
  speed; `full thr` > 0 back to full throttle *later*; `exit kph` > 0 faster exit.
- **Off track in a corner** (`off m` in `report`, `lap/ref` in `compare`): that lap's numbers
  for the corner describe an incident. If the *reference* went off, a "gain" there is the
  reference's mistake, not the driver's improvement; say so rather than coaching from it.
- **brake@ "-"** means no braking (taken flat or with a lift). `flat` corners in the map are taken
  without a real speed drop: coach those on commitment and line, not braking.
- **consistency**: `sd` is the standard deviation across laps. A brake-point sd above ~10 m or a
  min-speed sd above ~5 km/h means the driver hasn't found a reference point there yet.
- **Units**: km/h, metres from the start/finish line, seconds. Brake and Throttle are 0–1.
- **trace**: keep ranges short (100–400 m) and `--step` ≥ 5 m.
