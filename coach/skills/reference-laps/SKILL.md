---
name: reference-laps
description: Pull a faster driver's lap from Garage61 (the driver's own or a teammate's) and compare against it corner by corner to show where and how to find time. Use when the driver asks how a faster lap is driven, wants a reference or ghost lap, or has plateaued against their own best.
---

# Reference laps from Garage61

The driver's own best lap shows what they can already do. A faster driver's lap shows what is
possible. Use both: own best for consistency, a reference for new technique.

Use the `telemetry` skill for reading compare output.

## What's reachable

A personal Garage61 token reaches the driver's own Garage61 laps and those of their Garage61
**teammates** only, not every driver on Garage61. If nobody suitable is listed, say so and suggest
the driver joins or creates a Garage61 team with faster friends, or asks a coach to share laps.
`iagent garage61 status` checks the token and lists the teams.

## Steps

1. Find candidates: `iagent garage61 find --track T` (add `--car C` if asked). The list is each
   driver's best lap on the same track layout and car, fastest first, with `vs you` (percent
   slower/faster than the driver's best), rating, date, track temperature and whether the
   telemetry is viewable.
2. **Choose a reference**, and say why:
   - Telemetry must be viewable (`telemetry yes`).
   - Prefer a lap **1–3% faster** than the driver: close enough that the technique is
     transferable. A lap 5%+ faster is useful to see *where* time is, not *how much* to copy.
   - Prefer similar conditions (track temperature within ~10 °C) and a recent date (same
     iRacing season, so the same car model and tyre).
3. Import it: `iagent garage61 import <garage61 id>`; note the printed lap id (starts `g61-`).
   `iagent refs list --track T` shows what is already imported; don't re-import.
4. Compare: `iagent corners compare <driver's lap> <reference lap>`. Use the driver's best
   representative lap, or a typical one if the question is about habits.
5. Read it like a coach:
   - Rank corners by `delta`. Explain the top two or three with the *why*: braked later
     (`brake` < 0 means the driver braked earlier than the reference), carried more minimum
     speed, back to full throttle earlier, faster exit.
   - If the reference went off track in a corner (`off m` lap/ref), ignore that corner.
   - `trace` both laps through the biggest corner to show the shape of braking and throttle
     (e.g. the reference brakes harder but shorter, or trails off the brake to the apex).
   - Check with `iagent corners consistency` whether the driver's gap there is a habit or a
     one-off before making it the focus.
6. Answer: who the reference is (name, time, gap), the two or three corners with the most time
   and what the reference does differently there, then one **Focus:** for the next run. Write it
   to `notes/<track>.md` with the reference lap id so the next review can re-compare.

## If it fails

- "No Garage61 token": the driver needs a personal access token from
  https://garage61.net/developer, set as `GARAGE61_TOKEN` (or saved to
  `~/.config/iagent/garage61.token`).
- "No iRacing ids recorded": re-run `iagent ingest` on one of the driver's recordings of that
  track.
- An empty `find`: no teammate has driven this car there (or their laps are private).
