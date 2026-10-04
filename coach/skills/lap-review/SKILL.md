---
name: lap-review
description: Review the driver's laps from a session (default the latest) and give one clear thing to work on next, corner by corner. Use when the driver asks how a session went, where they are losing time, or what to focus on.
---

# Lap review

Goal: one specific, evidence-backed focus for the next run, not a list of everything. A driver
learning a track improves fastest by fixing one repeatable mistake at a time.

Use the `telemetry` skill for commands and how to read their output.

## Steps

1. **Pick the session.** If the driver named a track or session, use it. Otherwise run
   `iagent tracks` and `iagent laps list` and take the most recent session id (session ids are
   recording date-times, so the largest is the newest).
2. **Read existing notes**, if any: `<workspace>/notes/<track>.md` and
   `<workspace>/tracks/<track>/knowledge.md` (paths from `iagent workspace`; they may not exist
   yet). If a focus was set last time, check whether it improved; that comes first in your answer.
3. **Check corner names**: `iagent corners list --track T`. If most corners are unnamed, follow
   the `name-corners` skill first, so you can talk about "Pouhon", not "T9".
4. **Find the laps that count**: `iagent laps list --session S --representative`. If there are
   fewer than two, say so plainly; with one lap, compare it against the best lap from other
   sessions on the same track and car, if there is one.
5. **Find repeatable losses.** Run `iagent corners compare LAP` for each representative lap
   (against the session's best), and `iagent corners consistency --track T --session S`. A corner
   where most laps lose time, or with a large spread, is a habit; a single big loss is a one-off.
   Prefer habits. Ignore laps that are not representative.
6. **Explain the habit** from the corner numbers: braking earlier (`brake` < 0), less minimum
   speed, later full throttle, slower exit. If the numbers alone don't explain it, `trace` the
   best lap and a typical lap through that corner (from ~100 m before its entry to its exit).
7. **Optional: a faster reference.** If the driver has stopped improving against their own best,
   or asks how others drive it, use the `reference-laps` skill to compare against a teammate's
   Garage61 lap.
8. **Write it down.** Append a dated entry to `notes/<track>.md`: session id, best time, the
   focus, the evidence (numbers), and what to watch next time. Create the file if needed.

## Answer format

- Two to five sentences, then a one-line **Focus:**.
- Lead with the result: best lap, how consistent the representative laps were (spread in s).
- Name corners as the map does (name if set, otherwise `T7` plus distance).
- Every number must come from CLI output. Round sensibly (0.1 s, 5 m, 1 km/h).
- Say what to *do* ("brake 15 m later into Bruxelles", "carry 5 km/h more to the apex of
  Pouhon"), not just where time was lost.
