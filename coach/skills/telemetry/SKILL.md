---
name: telemetry
description: Find, inspect and compare the driver's recorded iRacing laps with the `iagent` CLI. Use whenever a question needs lap times, splits, speeds, brake points or traces from real telemetry.
---

# Telemetry with the `iagent` CLI

All lap data comes from the `iagent` command. Run it in a shell, one command at a time. The default
output is a compact table, which is usually all you need; add `--json` when you need exact fields
(every command except `trace`, which prints CSV). Don't pipe output through other programs: the
commands' own filters (`--session`, `--representative`, `--from/--to`) are enough. Never invent
numbers: every figure you report must come from a command's output. If `iagent` is not on the PATH, the driver must install
it (`uv tool install --editable <repo>`); say so and stop.

## Orient first

```sh
iagent workspace --json      # where the workspace (laps, notes/, scratch/) is
iagent tracks --json         # which track/car combinations exist, with their keys
```

`track` and `car` in that output are **keys** (e.g. `spa-2024-up`, `formulair04`). Use them in
filters. Laps on different keys must never be compared (different layout or car).

## Commands

| Command | Gives you |
| --- | --- |
| `iagent laps list --track T --car C [--session S] [--valid-only] [--representative] --json` | laps with `lap_time`, `vs_best_pct`, `off_track_s`, `valid`, `reasons` |
| `iagent laps show LAP --json` | top/min speed, % at full throttle, % braking, 10 distance splits |
| `iagent laps compare LAP [REF] --json` | per-section `delta_s` vs a reference (default: fastest other valid lap) |
| `iagent laps trace LAP --from M --to M [--step M] [--channels ...]` | CSV samples along the lap |

Run `iagent <command> --help` for every option.

## Reading the numbers

- **Lap ids** look like `20250723-202727-L005`: session (recording date-time) + lap number.
  `L000` is usually a partial lap from leaving the pits.
- **valid** is structural only: the lap is complete, didn't touch pit road, and the car wasn't
  reset. `reasons` says why not.
- **representative** = valid and within 5% of the best valid lap. Use these for coaching. Slower
  valid laps are spins, recoveries, traffic or cool-down laps: mention them only if asked.
- **off_track_s** is information, not a penalty. A best lap with 1 s off track is still the best lap.
- **Sections** in `show`/`compare` are equal-distance slices of the lap, *not corners*: one corner
  can straddle two sections. Before naming a corner, check the region with `trace`.
- **delta_s > 0**: the lap was *slower* than the reference in that section. `biggest_losses`
  lists the worst sections.
- **brake_start_m** is where braking *began* within that section (null if braking only continued
  from the previous one). Distances are metres from the start/finish line.
- **Units**: speeds in km/h, distances in m, times in s. Brake and Throttle are 0–1.
- **trace** output: keep ranges short (100–400 m) and `--step` ≥ 5 m; a whole lap at 1 m is
  thousands of rows.

## Typical moves

- *"How was my session?"*: `laps list --representative`, then `compare` each representative lap
  against the best.
- *"Where am I losing time?"*: run `compare` for several laps and look for sections that lose
  time **repeatedly**, not just once.
- *"What am I doing differently there?"*: `trace` both laps over that section with
  `--channels Speed,Brake,Throttle,Gear` and compare brake point, minimum speed and when the
  throttle comes back.
