---
name: name-corners
description: Attach names and known characteristics to a track's derived corners (e.g. Spa's La Source, Eau Rouge, Pouhon) and record what is known about each corner. Use when a track's corners are unnamed, when the driver asks about a corner by name, or when the driver corrects a name.
---

# Naming corners

Corners are derived from the driver's laps and numbered T1, T2, … in lap order. Names come from
outside the telemetry, so treat every source as fallible and record where each name came from.

## Steps

1. `iagent corners list --track T` to see the map: direction, entry/apex/exit distances (m from
   the start/finish line), reference minimum speed, current names and their sources.
2. `iagent corners landmarks --track T` for CrewChief's landmark data (covers ~25 iRacing tracks).
   It shows which derived corners fall inside each named landmark. Then
   `iagent corners landmarks --track T --apply` names the unambiguous one-to-one matches
   (source `crewchief`, confidence `medium`). Generic names like "turn9" are never applied.
3. Fill the gaps yourself, in this order of trust:
   - **The driver's corrections** always win: `--source driver --confidence high`.
   - **Researched knowledge**: if you can search the web, look up the track's corner names and
     order for the current layout. Use `--source web`, and `--confidence high` only if two sources
     agree.
   - **Your own knowledge** of the circuit: `--source model --confidence low` unless you are sure.
   Match by order around the lap, direction (L/R), and rough distance and speed (a hairpin is
   slow, a kink is flat).
4. Set each name: `iagent corners name --track T 3 "Raidillon" --source web --confidence high`.
   - Fix misspellings in landmark data ("radillion" → "Raidillon", "stavlot" → "Stavelot").
   - Chicanes are split into two corners: name both, e.g. "Les Combes (entry)" / "Les Combes
     (exit)", or by the track's own convention if it numbers them separately.
   - Leave a corner unnamed rather than guess wildly; `T13 (fast right kink, ~5,400 m)` is fine.
5. Record what is known about each named corner in `<workspace>/tracks/<track>/knowledge.md`:
   character (e.g. "Eau Rouge/Raidillon: flat-out compression and crest; commitment, not
   braking"), common mistakes, reference points if known, and the sources you used. Keep it short.
6. Tell the driver which names are low confidence and ask them to correct any that are wrong.
