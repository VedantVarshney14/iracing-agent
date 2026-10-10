---
name: live-rules
description: Set what the live coach watches for on track and what it says then, as rules (a trigger, a condition, an action) that the live service runs without a model. Use when the driver wants a specific call-out while driving ("tell me if I brake early for the Bus Stop", "warn me when I run wide at Pouhon"), when a debrief or lap review finds one habit worth watching live, or to review, change or switch off the rules already set.
---

# Live rules

The live coach (`iagent live run`, or the Coaching tab) already cues corners, gives feedback
after a corner that cost time and keeps one focus. A rule adds something specific on top: *at
this moment, if this is true, say (or log) this*. Rules run in the live service at 60 Hz with
no model involved, so they must be exact. Backtest every rule before it goes live.

## Steps

1. **Know the problem first.** Use `lap-review` / the notes (`workspace/notes/<track>.md`) to find
   the habit worth watching, e.g. braking 15 m early for T1 on most laps. One rule per habit.
2. **See what's available:** `iagent rules vars` (or `--trigger corner_exit`) lists every trigger
   and its variables. Corner metrics at `corner_exit` use the same numbers as
   `iagent corners compare`; `brake_diff_m` < 0 means braked earlier than the reference.
   `iagent rules list --track T` shows rules already set: change one with `add --replace`
   rather than adding a near-duplicate.
3. **Write the rule** and add it (it's saved as a draft):

   ```bash
   iagent rules add '{"id": "t1-early-brake", "track": "spa-2024-up",
     "description": "Brakes ~15 m early for La Source (lap review, 3 sessions).",
     "when": {"corner_exit": "La Source"}, "if": "brake_diff_m < -10",
     "action": {"say": "La Source: braked {round5(-brake_diff_m)} metres early."},
     "limits": {"cooldown_laps": 1}}'
   ```

   The command checks fields, variables and corner names and says what to fix.
4. **Backtest:** `iagent rules backtest <id>` replays the last few recorded sessions through the
   live coach with the rule. Read it:
   - **Fires on most laps:** a nag. Tighten the condition, add `in_a_row`, or a cooldown.
   - **Never fires:** check the threshold against the values printed for each firing, or use a
     `log` action first to see the numbers.
   - **Dropped lines:** something else was talking (often the built-in cue for the same corner)
     or there was no straight long enough. Shorten the text, change the priority, or rewrite the
     corner's cue instead (`iagent cues set`).
5. **Activate:** `iagent rules activate <id>`. A running coach picks it up within ~10 s. Tell the
   driver, in one line, what they'll hear and when.
6. Later, when the habit is fixed (lap review shows it), `iagent rules deactivate <id>` or
   `archive` it, and say so.

## Writing good rules

- **Spoken text is short.** About 14 characters a second; a corner-exit line must fit the next
  straight, a cue (`"at"` trigger) must finish before the corner. Name the corner, then one fix.
- **Feedback after, cues before.** `corner_exit` + `feedback` priority for what happened;
  `{"at": "T1"}` (the reference brake point) + `cue` priority for a reminder on the approach.
  Use `"lead_s"` / `"offset_m"` to move it; `"T1 apex"`, `"T1 exit"`, `"T1 entry"` or metres work too.
- **Don't nag.** `in_a_row: 2` for things that only matter when repeated; `cooldown_laps`,
  `max_per_lap`, `once`. Rules ignore out laps, cool-downs and moments by default
  (`pushing_only`); set it false only for things like pit or tyre reminders.
- **Missing values are quiet.** A condition on a missing value is false, and a line whose text
  needs a missing value isn't said, so `brake_diff_m < -10` simply doesn't fire on a lap the
  corner was taken flat.
- **Track-wide rules** (no `track`) work everywhere: lap-time calls (`{"lap": "complete"}`, e.g.
  `"if": "new_best"`), pit reminders (`{"pit": "exit"}`), schedules (`{"every_laps": 5}`).
- `wake` hands the event to the coach (you); keep it for things that need thinking, not
  for anything the rule can say itself.

## Examples

```json
{"id": "pouhon-wide", "track": "spa-2024-up", "when": {"corner_exit": "Pouhon"},
 "if": "off_track_m > 5", "in_a_row": 2,
 "action": {"say": "Pouhon: wide twice now. Tighter entry."}}

{"id": "bus-stop-lift", "track": "spa-2024-up", "when": {"at": "Bus Stop", "lead_s": 1.0},
 "if": "not learning", "action": {"say": "Bus Stop. Just a lift."}}

{"id": "new-best", "when": {"lap": "complete"}, "if": "new_best",
 "action": {"say": "New best, {say_time(lap_time)}.", "priority": "summary"}}

{"id": "slow-exit-t3", "track": "okayama-full", "when": {"corner_exit": 3},
 "if": "exit_speed_diff_kph < -6 and throttle_diff_m > 20",
 "action": {"say": "Turn 3: late on the throttle, {round(-exit_speed_diff_kph)} down on exit."}}
```
