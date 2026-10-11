# Coaching while you drive

`iagent live run` (or the Coaching tab in [the browser UI](browser-ui.md)) coaches you on track,
without a model in the real-time loop. It's meant to sound like an engineer on the radio, not a
recording: brief when you're busy, fuller when you have time. How it works inside is in
[ARCHITECTURE.md](../ARCHITECTURE.md#7-the-live-pipeline).

```bash
uv sync --extra voice                                    # Pocket TTS + audio output (PyTorch, CPU)
iagent cues build --track spa-2024-up --car formulair04  # see / rebuild the cues (built on first use)
iagent cues set --track spa-2024-up --car formulair04 1 "La Source. Hairpin right. Big stop, second gear." \
  --short "La Source. Big stop."                         # the reminder once it's been heard
iagent live run                                          # on the sim PC: waits for iRacing
iagent live run --replay session.ibt --start 700         # anywhere: hear a recording in real time
iagent live run --replay session.ibt --print --speed 20  # what it would say, fast, no audio
```

Cues follow the fastest Garage61 teammate lap for the track and car, else your own best, and work
on a track you've never driven if a teammate's lap is imported. Speech is
[Pocket TTS](https://github.com/kyutai-labs/pocket-tts) on the CPU (2 threads). Each session's
log, debrief and debug log are kept in `workspace/sessions/live/`.

## What it says

- **Corner cues.** Approaching each corner: *"La Source, hairpin right. Hard brake, second
  gear."*, timed to finish at the reference lap's brake point at your current speed. Corners close
  together share a cue. The first two laps cue every corner; after that only the corners that
  went badly, so it goes quiet as you learn. Once you've heard a cue in full, it shortens to a
  reminder: *"La Source. Hard brake."*
- **Feedback.** After a corner that cost time, on the next straight: *"Turn 5: braked 20 metres
  early. Brake later."*, and a hint in that corner's cue next lap. The same mistake again is a
  repeat (*"Turn 5 again: ..."*), and fixing it gets a *"Turn 5: better."*
- **More when there's time.** Feedback has a longer version (the why and the how), said instead
  when you're not pushing. On a cool-down lap (15 s or more off the pace) the coach debriefs: the
  corner costing the most over your last laps at pace, what to change and how, and how
  consistent the laps were.
- **One focus at a time.** After the learning laps the corner losing the most (over the last two
  laps at pace) becomes the focus: *"Focus now: Turns 15 and 16. Just a lift, no brakes."* It's
  cued every lap; other corners speak up only for a big loss or an off. Once you match the
  reference there twice, the next focus is picked.
- **Only while you're pushing.** Pace over the last 400 m is compared with your own best lap. On
  an out lap, a cool-down or just after a moment (10%+ slower) the coach goes quiet and doesn't
  judge the corners, then picks up again once you're within 5%.
- **Lap summary** at the line, short enough for a short straight: *"2 27.0, 5 tenths down."*, or
  where it went on a lap with a moment: *"1 44.2. Lost it at Turn 7."*

## The engineer

Where the model has time to answer, it does the talking, and it's the same conversation all
session, like one engineer on the radio. Your Claude Code (`claude -p`) gives a radio check and
the plan as you head out (from its notes), hears every cue and lap time, answers your questions
and rules that ask it (`ask_engineer`), words the cool-down debrief, and writes up its notes
(`workspace/notes/<track>.md`) when the session ends; the next session's engineer starts from
them. It's given the numbers, not asked to work them out.

Corner cues stay fixed text, because they must be instant and exactly timed, but the coach can
rewrite them between sessions (`iagent cues set`). No reply in time, or no `claude`: the coach's
own phrasing is said. `--no-engineer` turns it off.

## With CrewChief

CrewChief does the spotter's job (cars alongside, lap times and personal bests, gaps, fuel,
tyres, flags, pit calls); the coach does technique and leaves all of that to it. It never talks
over the spotter (nothing new starts with a car alongside), and when CrewChief is running
(`--crewchief auto`, the default, detects it; `on`/`off` to force it) the lap summary drops the
lap time and gap and says only where the lap went (*"Most time lost at Pouhon."*), and for a few
seconds after the line only corner cues are said, while CrewChief reads the time.
`iagent rules add` warns about rules that would say what CrewChief already does.

## Rules: what else to watch for

Everything the live coach says goes through **rules**: an event, a condition and what to do,
run with no model involved. Its own cues, feedback, summaries and debriefs are built-in rules;
the coach (or you) can add more. The `live-rules` skill writes them.

```bash
iagent corners list --track spa-2024-up --json   # the corners' numbers: Pouhon is 9
iagent rules add '{"id": "pouhon-wide", "track": "spa-2024-up",
  "when": {"event": "corner_exit", "corner": 9}, "if": "off_track_m > 5", "in_a_row": 2,
  "actions": [{"say": "Pouhon: wide twice now. Tighter entry."}]}'
iagent rules backtest pouhon-wide        # replay recent sessions: where it fires, what's said
iagent rules activate pouhon-wide        # live (a running coach picks it up within ~10 s)
iagent rules list
iagent rules vars                        # every event, its fields, the shared state, functions
iagent rules schema                      # the exact format, as JSON Schema
```

- **One strict format.** A rule is checked against a JSON Schema generated from the event
  definitions. Corners are the corner map's numbers (`iagent corners list --json`), not names. A
  rule the schema rejects says which field is wrong.
- **Events:** `when` names any event the live coach knows, with filters on its fields and an
  optional `where` condition: a point on track (`{"event": "approach", "at": {"corner": 9}}`,
  timed to finish before the reference brake point), a corner exit with that corner's numbers
  against the reference (`brake_diff_m`, `min_speed_diff_kph`, `off_track_m`, ...), a lap
  (`new_best`, `gap_s`, ...), pit entry/exit, pace or focus changes, and a condition on live
  channels (`{"event": "frame", "edge": "speed_kph > 280"}`). `iagent rules vars` lists them all.
- **Conditions and text** use a small, safe expression language: arithmetic, comparisons,
  `and`/`or`/`not`, `in`, and helpers like `round5()` and `say_time()`. Spoken text has
  `{expression}` holes. A missing value makes the condition false rather than failing.
- **Actions** (run in order): `say` (with an optional `"long"` version for when you're not
  pushing), `log`, and `ask_engineer` (the engineer answers on the radio in its own words, or
  stays silent).
- **Limits:** cooldowns, per-lap and per-session caps, `once`, `in_a_row`. Rules ignore out
  laps, cool-downs and moments unless `"pushing_only": false`.
- **Safe to change:** a new or edited rule is a draft until it has been backtested and activated.
  Rules live in `workspace/rules/<track>/` (or `rules/_any/` for every track). Each session's log
  records every firing.
