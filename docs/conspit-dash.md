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

## Planned build

- **Layout**: a Dash Studio project at 800×268: gear, speed, RPM bar, lap and delta, fuel, plus a
  coach strip (current cue, focus corner, last feedback). Lives in `simhub/`.
- **Feed**: the live coach publishes its current line and focus to a small local file or port. A
  SimHub plugin (C#, the one planned in ARCHITECTURE.md) reads it and exposes properties such as
  `Coach.Cue` that the dashboard binds to.
