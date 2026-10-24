# Conspit 300GT dashboard

A SimHub dashboard for the 300GT's built-in screen (2.99", about 800×268) that also shows what the
coach is saying. Status: setup unverified; the screen's SimHub support is new and in beta.

## Check the screen takes a SimHub dashboard (sim PC)

Do this before building anything.

1. Update the 300GT firmware and install the newest Conspit Link 2.0 (beta channel if needed).
2. In Conspit Link, turn on **SimHub Mode** for the wheel.
3. Install the Conspit SimHub plugin and enable it in SimHub's plugin list.
4. In SimHub, add the 300GT as a device and pick any stock dashboard.
5. Does the wheel screen show it, at the right size and refresh rate, with iRacing running?

If step 5 fails, stop and check Conspit's release notes or Discord; there's nothing to build yet.

## The coach feed

A live session (`iagent live run`, or the Coaching tab in `iagent ui`) keeps
`~/.iagent/dash.json` up to date (`--dash-file` moves it for `live run`). It's written whole, and
only when something on it changes: a few times a lap.

```json
{"length_m": 7004.0, "seq": 41,
 "corner": {"id": 5, "name": "La Source", "direction": "R", "action": "BRAKE", "hard": true,
            "gear": 2, "target_m": 6812.0},
 "cue": "BRAKE 20 m LATER",
 "focus": "FOCUS 8",
 "trace": {"last_brake": [0, 0, 0.42, ...], "last_throttle": [...],
           "target_brake": [...], "target_throttle": [...], "apex": 0.52, "arrow": [0.21, 0.27]},
 "strip": {"sectors": [{"from": 0, "to": 0.06, "state": "gain"}, ...], "next": 0.62,
           "focus": [[0.70, 0.76]]}}
```

- `corner` is the coming one, from the moment the car passes the previous corner's exit.
  `action` is `BRAKE`, `LIFT` or `FLAT` on the reference lap, `target_m` where it brakes (or just
  before turn-in).
- `cue` is a few words on what to change, from the last time the corner was driven at pace;
  empty when there's nothing to change.
- `trace`: brake and throttle (0-1) through the corner, 96 points from 120 m before the reference
  brake point to 60 m past the exit. `last_*` is the driver's last lap (null on the first), `target_*`
  the reference lap's. `apex` and `arrow` (last lap's brake point, then the reference's; only when
  they differ by 15 m or more) are 0-1 across the trace.
- `strip`: each corner's share of the lap this lap: `gain`, `small` (lost under 0.08 s), `loss`,
  or `todo` (not driven yet); the next corner's turn-in and the focus corners, 0-1 along the lap.

This works with `--replay` too, so you can build the dashboard without driving.

## The SimHub plugin

`simhub/CoachPlugin` reads that file and gives SimHub:

| Property | Value |
|---|---|
| `CoachPlugin.Corner`, `.CornerName` | the coming corner's number and name |
| `CoachPlugin.Action`, `.Hard` | `BRAKE`, `LIFT` or `FLAT`; true for a hard stop (a red chip) |
| `CoachPlugin.Gear`, `.Direction` | `G2`; `L` or `R` |
| `CoachPlugin.ToGoM` | metres to the brake point, worked out every frame from the car's position |
| `CoachPlugin.Cue`, `.Focus` | the few words; `FOCUS 8` |
| `CoachPlugin.TraceImage`, `.StripImage` | paths to the pedal trace (776×84) and lap strip (776×12), drawn as in the design |
| `CoachPlugin.Seq` | goes up with every change to the feed |

The plugin draws the trace and strip itself because Dash Studio has no way to plot a list of
values. It writes them to `~/.iagent/trace-0.png` / `trace-1.png` (and `strip-*`) in turn, so a
dashboard that caches an image by its path still sees the new one.

On the sim PC (needs the .NET SDK; written against the SimHub plugin SDK but not yet built or run):

```
cd simhub\CoachPlugin
dotnet build -p:SimHubDir="C:\Program Files (x86)\SimHub\"
```

Close SimHub first. The build copies `CoachPlugin.dll` into the SimHub folder. Start SimHub, enable
"iagent coach" under Plugins, run `iagent live run --replay ... --start ...`, and the properties
should change in SimHub's Properties list.

## The dashboard

The design is `simhub/dash-design.html` (open it in a browser). Build it in Dash Studio (New
dashboard, 800×268, the 300GT as the target device). Each area has its own band of the screen, so
nothing overlaps:

| Area (y, px) | What | Bind to |
|---|---|---|
| RPM bar (0–16) | 20 rounded segments | `DataCorePlugin.GameData.CarSettings_RPMPercent` |
| Top left (18–122) | Next corner: number, name, action/gear/direction chips, distance | `CoachPlugin.Corner`, `.CornerName`, `.Action` (red when `.Hard`), `.Gear`, `.Direction`, `.ToGoM` |
| Top centre (18–122) | Gear, big; speed and brake bias under it | `DataCorePlugin.GameData.Gear`, `DataCorePlugin.GameData.SpeedMph` |
| Top right (18–122) | Focus corner; last lap and delta | `CoachPlugin.Focus`, `DataCorePlugin.GameData.LastLapTime`, `PersistantTrackerPlugin.SessionBestLiveDeltaSeconds` |
| Cue (128–156) | A few words in amber | `CoachPlugin.Cue` |
| Trace (160–244) | An "Image from file" item at x 12 | `CoachPlugin.TraceImage` |
| Lap strip (250–262) | An "Image from file" item at x 12, with a white dot over it for the car | `CoachPlugin.StripImage`; the dot's left: `12 + 776 * DataCorePlugin.GameData.TrackPositionPercent` |

Property names are SimHub's; check them in its Properties list if one doesn't resolve (in
particular `TrackPositionPercent`, which the plugin also uses for `ToGoM`: if it runs 0–100
rather than 0–1, divide by 100). Whether "Image from file" reloads when the file changes is
unchecked; the two alternating files are there for that. Save the dashboard export in `simhub/`.

## If the wheel screen won't take it

Any SimHub display shows the same dashboard: the CSD Nano, a phone or tablet on SimHub's web
dashboard, or a second monitor.
