# Laps, corners and reference laps

The `iagent` CLI does the telemetry work: ingesting recordings, judging laps, mapping corners and
comparing laps corner by corner. Every command has `--help`; agent-facing ones take `--json`
(`trace` prints CSV).

```bash
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

## The workspace

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
  rules/<track>/                        rules for the live coach (rules/_any/ for every track)
  sessions/live/                        coached sessions' logs, debriefs and debug logs
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

## How laps are judged

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

## Corners

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

## Reference laps and ghosts from Garage61

Compare against faster drivers' laps from [Garage61](https://garage61.net), and drive against
their ghosts in iRacing. Create a personal access token at https://garage61.net/developer and
provide it as `GARAGE61_TOKEN` (or `GARAGE61_PAT`) in the environment or a `.env` file in the
directory you run `iagent` from, or save it to `~/.config/iagent/garage61.token`. The browser
UI's library can also add one.

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
