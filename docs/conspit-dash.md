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

`iagent live run` keeps the latest line of each kind in `~/.iagent/dash.json` (`--dash-file` to
move it), written whole each time:

```json
{"cue": "La Source. Big stop.", "feedback": "Turn 5: brake later.",
 "summary": "2 27.0, 5 tenths down.", "focus": "Turns 15 and 16. Just a lift.", "seq": 12}
```

`seq` goes up with every line, so a dashboard can flash the strip when it changes. This works with
`--replay` too, so you can build the dashboard without driving.

## The SimHub plugin

`simhub/CoachPlugin` reads that file and exposes `CoachPlugin.Cue`, `.Feedback`, `.Summary`,
`.Focus` and `.Seq` as SimHub properties. On the sim PC (needs the .NET SDK; written against the
SimHub plugin SDK but not yet built or run):

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
| Top left (18–122) | Next corner: number, name, brake/gear/direction chips, distance | not fed yet |
| Top centre (18–122) | Gear, big; speed and brake bias under it | `DataCorePlugin.GameData.Gear`, `DataCorePlugin.GameData.SpeedMph` |
| Top right (18–122) | Focus corner; last lap and delta | `CoachPlugin.Focus`, `DataCorePlugin.GameData.LastLapTime`, `PersistantTrackerPlugin.SessionBestLiveDeltaSeconds` |
| Cue (128–156) | A few words in amber, e.g. "BRAKE 18 m LATER" | `CoachPlugin.Cue` |
| Trace (160–244) | Next corner's brake and throttle: last lap as solid lines, the target as a soft band with a dotted centre line, an arrow for the change | not fed yet |
| Lap strip (250–262) | Mini-sectors coloured by time gained or lost, car position, next and focus corners | not fed yet |

Flash the cue for 3 s when `CoachPlugin.Seq` changes. Property names are SimHub's; check them in
its Properties list if one doesn't resolve. Save the dashboard export in `simhub/`.

The "not fed yet" areas need the feed to send, per corner: the corner's name and cues, last lap's
brake and throttle sampled by distance, the target traces, and the mini-sector times. The cue on
the wheel should be a few words, not the coach's full spoken line.

## If the wheel screen won't take it

Any SimHub display shows the same dashboard: the CSD Nano, a phone or tablet on SimHub's web
dashboard, or a second monitor.
