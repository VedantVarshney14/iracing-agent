---
name: lap-review
description: Review the driver's laps from a session (default the latest) and give one clear thing to work on next. Use when the driver asks how a session went, where they are losing time, or what to focus on.
---

# Lap review

Goal: one specific, evidence-backed focus for the next run, not a list of everything. A driver
learning a track improves fastest by fixing one repeatable mistake at a time.

Use the `telemetry` skill for commands and how to read their output.

## Steps

1. **Pick the session.** If the driver named a track or session, use it. Otherwise run
   `iagent tracks --json` and `iagent laps list --json` and take the most recent session id
   (session ids are recording date-times, so the largest is the newest).
2. **Read existing notes**, if any: `<workspace>/notes/<track_key>.md` (path from
   `iagent workspace --json`; the folder may not exist yet). If a focus was set last time, check whether it improved; that
   comes first in your answer.
3. **Find the laps that count**: `iagent laps list --session S --representative --json`. If there
   are fewer than two, say so plainly. With one lap, `show` it and compare it against the best
   lap from other sessions on the same track and car, if one exists.
4. **Find repeatable losses.** Compare every representative lap against the session's best
   (`iagent laps compare LAP --json`, or `compare LAP BEST`). A section where most laps lose time
   is a habit; a single big loss is a one-off. Prefer habits.
5. **Explain the habit.** `trace` the best lap and one typical lap over that section (plus
   ~100 m before it, to catch the braking zone). Identify the concrete difference: braking
   earlier, a lower minimum speed, later throttle, an extra gear.
6. **Name the place** in words the driver will recognise: the corner name if you are confident
   of it for this track and distance, otherwise "the braking zone about N m after the line" or
   "the slow corner at N m". Say how sure you are if you name a corner.
7. **Write it down.** Append a dated entry to `notes/<track_key>.md`: session id, best time, the
   focus, the evidence (numbers), and what to watch next time. Create the file if needed.

## Answer format

- Two to five sentences, then a one-line **Focus:**.
- Lead with the result: best lap, how consistent the representative laps were (spread in s).
- Every number must come from CLI output. Round sensibly (0.1 s, 5 m, 1 km/h).
- Say what to *do* ("brake 15 m later into …", "carry 5 km/h more to the apex of …"), not just
  where time was lost.
