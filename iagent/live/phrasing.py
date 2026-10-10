"""Everything the coach says, worded in one place.

Components decide what matters; this module decides how it sounds. Templates read like an
engineer on the radio: the short form for a driver who's busy, the longer one (what, why, how)
for one with time to listen.

Causes of a corner's lost time are definitions: how to spot one, and how to say it. A new cause
is a new `Cause` in `CAUSES` (they're tried in order; the first that fits is the clearest).
"""

from dataclasses import dataclass
from typing import Callable

from iagent.live.expr import round5, say_gap, say_time

__all__ = ["CAUSES", "Cause", "cause_of", "corner_name", "cue_label", "debrief_lines", "consistency",
           "summary", "again", "better", "round5", "say_gap", "say_time"]


def corner_name(c) -> str:
    return c.name or f"Turn {c.id}"


def _diff(a, b):
    return None if a is None or b is None else a - b


@dataclass(frozen=True)
class Cause:
    name: str
    doc: str
    # (this lap's corner metrics, the reference's, time lost, settings) -> how much, or None if it isn't this
    detect: Callable[[dict, dict, float, object], float | None]
    short: Callable[[str, float, dict], str]  # (corner name, amount, ref metrics) -> said after the corner
    hint: Callable[[dict], str]  # (ref metrics) -> added to the corner's cue next lap
    longer: Callable[[str, float, dict, dict], str]  # (who, amount, ref, mine) -> the why and the how
    any_loss: bool = False  # worth saying even when the corner cost little (going off)


def _lift(ref: dict) -> bool:
    return (ref.get("min_throttle") or 1) < 0.9


def _early_longer(who, amount, ref, mine):
    slow = _diff(mine.get("min_speed_kph"), ref.get("min_speed_kph"))
    extra = " and you're slower at the apex too, so it costs you twice" if slow is not None and slow <= -3 else ""
    return (f"{who}'re braking about {round5(amount)} metres before the reference{extra}. There's more room than "
            "it feels: move the brake point a few metres a lap, same pressure, and let the car tell you when it's enough.")


CAUSES: list[Cause] = [
    Cause("off", "ran off track (amount: metres off)",
          lambda m, r, d, s: float(m["off_track_m"]) if (m.get("off_track_m") or 0) >= s.off_track_m else None,
          lambda n, a, r: f"{n}: you ran off track. Brake a touch earlier and tidy the entry.",
          lambda r: "Tidy entry, you ran wide last lap.",
          lambda who, a, r, m: (f"{who} ran wide, {round(a)} metres off track. That usually starts at the entry: brake a "
                                "touch earlier, get the car turned before you go back to the throttle, and build up "
                                "from there once it sticks."),
          any_loss=True),
    Cause("early_brake", "braked earlier than the reference (amount: metres)",
          lambda m, r, d, s: (-(m["brake_m"] - r["brake_m"]) if m.get("brake_m") is not None and r.get("brake_m") is not None
                              and m["brake_m"] - r["brake_m"] <= -s.brake_m else None),
          lambda n, a, r: f"{n}: braked {round5(a)} metres early. Brake later.",
          lambda r: "Brake later than last lap.",
          _early_longer),
    Cause("late_brake", "braked later and lost the exit (amount: metres)",
          lambda m, r, d, s: (m["brake_m"] - r["brake_m"] if m.get("brake_m") is not None and r.get("brake_m") is not None
                              and m["brake_m"] - r["brake_m"] >= s.brake_m
                              and (m.get("min_speed_kph") or 0) < (r.get("min_speed_kph") or 0) else None),
          lambda n, a, r: f"{n}: braked {round5(a)} metres late and lost the exit.",
          lambda r: "Brake a little earlier than last lap.",
          lambda who, a, r, m: (f"{who}'re braking about {round5(a)} metres late and it's costing the exit. Brake a "
                                "little earlier, get it slowed and turned, and you'll be back on the power sooner. Exit "
                                "speed is worth more than entry.")),
    Cause("no_brake_needed", "braked where the reference doesn't",
          lambda m, r, d, s: 0.0 if m.get("brake_m") is not None and r.get("brake_m") is None else None,
          lambda n, a, r: f"{n}: no need to brake there." + (" A lift is enough." if _lift(r) else ""),
          lambda r: "Just a lift, no brakes." if _lift(r) else "Stay off the brakes.",
          lambda who, a, r, m: (f"{who} don't need the brakes there, {'a lift is enough' if _lift(r) else 'it is flat'}. "
                                f"Next time try {'just a lift' if _lift(r) else 'staying flat'} and trust the grip, a "
                                "little more each lap.")),
    Cause("slow_apex", "slower at the apex (amount: km/h)",
          lambda m, r, d, s: (-dv if (dv := _diff(m.get("min_speed_kph"), r.get("min_speed_kph"))) is not None
                              and dv <= -s.min_speed_kph else None),
          lambda n, a, r: f"{n}: {round(a)} kilometres an hour slower at the apex. Carry more speed in.",
          lambda r: "Carry more speed in.",
          lambda who, a, r, m: (f"{who}'re about {round(a)} kilometres an hour slower at the apex. Ease off the brake "
                                "more gradually as you turn in and let the car roll more speed to the apex. Don't brake "
                                "later, just release it smoother.")),
    Cause("late_throttle", "full throttle later than the reference (amount: metres)",
          lambda m, r, d, s: (dt if (dt := _diff(m.get("full_throttle_m"), r.get("full_throttle_m"))) is not None
                              and dt >= s.throttle_m else None),
          lambda n, a, r: f"{n}: full throttle {round5(a)} metres later than the reference. Get on it earlier.",
          lambda r: "Earlier on the throttle.",
          lambda who, a, r, m: (f"{'Full throttle comes' if who == 'You' else who.split(':')[0] + ': full throttle comes'} "
                                f"about {round5(a)} metres later than the reference. Once you're past the apex, open the "
                                "steering and commit to the throttle earlier, a bit more each lap.")),
]
CAUSE_BY_NAME = {c.name: c for c in CAUSES}


def cause_of(mine: dict, ref: dict, delta: float, settings) -> tuple[Cause | None, float | None]:
    """The clearest cause of a corner's lost time (the first that fits), with how much."""
    for cause in CAUSES:
        if not cause.any_loss and delta < settings.loss_s:
            continue
        amount = cause.detect(mine, ref, delta, settings)
        if amount is not None:
            return cause, amount
    return None, None


def advice(name: str, cause: Cause | None, amount: float | None, ref: dict, mine: dict) -> dict:
    """Short advice, the cue hint and the longer version for a corner."""
    if cause is None:
        return {"advice": None, "hint": None, "longer": None}
    return {"advice": cause.short(name, amount, ref), "hint": cause.hint(ref),
            "longer": cause.longer(f"{name}: you", amount, ref, mine)}


def longer(name: str, cause_name: str | None, amount, ref: dict, mine: dict, named: bool = True) -> str | None:
    cause = CAUSE_BY_NAME.get(cause_name) if cause_name else None
    if cause is None:
        return None
    return cause.longer(f"{name}: you" if named else "You", amount, ref, mine)


def again(name: str, advice_text: str) -> str:
    """The same mistake as last lap, said as a repeat."""
    rest = advice_text.split(": ", 1)[-1]
    return f"{name} again: {rest[0].lower()}{rest[1:]}"


def better(name: str) -> tuple[str, str]:
    return f"{name}: better.", f"That's better at {name}. Right with the reference there, keep that."


def cue_label(names: list[str | None], corners: list[int]) -> str:
    """How a cue's corners are said: "Pouhon", "Turns 15 and 16", "Stavelot and Paul Frere"."""
    if all(names):
        return " and ".join(names)
    if len(corners) == 1:
        return names[0] or f"Turn {corners[0]}"
    return f"Turns {corners[0]} and {corners[-1]}"


def with_hint(base: str, hint: str | None, hint_corner_name: str | None) -> str:
    """A cue with last lap's hint; about the cue's second corner, say which."""
    if not hint:
        return base
    if hint_corner_name:
        hint = f"{hint_corner_name}: {hint[0].lower()}{hint[1:]}"
    return f"{base} {hint}"


def summary(lap_time: float, ref_time: float | None, worst_name: str | None, moment_name: str | None,
            crewchief: bool) -> str | None:
    """Short enough (~3 s) for the straights of a busy track: "1 33.9, 3 seconds down. Worst: Turn 6."
    With CrewChief reading the lap time, only what it can't say: "Most time lost at Turn 6.", or nothing."""
    text = "" if crewchief else say_time(lap_time)
    if moment_name is not None:  # the gap means nothing: say where it went
        return f"{text + '. ' if text else ''}Lost it at {moment_name}."
    if not crewchief:
        if not ref_time:
            return text + "."
        gap = lap_time - ref_time
        text += f", {say_gap(gap)} {'down' if gap > 0 else 'up'}."
    if worst_name:
        text += f" Most time lost at {worst_name}." if crewchief else f" Worst: {worst_name}."
    return text.strip() or None


# How a debrief opens, in turn, so it doesn't sound like a recording.
DEBRIEF_OPENERS = ("While you cool them down: ", "Okay, easy lap. ", "Right, while it's quiet: ")


def debrief_lines(topics: list[dict], lap_times: list[float], n: int) -> list[str]:
    """The coach's own debrief: TOPICS (see DebriefTracker), laps' consistency; N-th debrief."""
    opener = DEBRIEF_OPENERS[n % len(DEBRIEF_OPENERS)]
    lines = []
    for i, t in enumerate(topics):
        lead = (opener if i == 0 else "And ") + (
            f"{t['name']} is still the focus. " if t["in_focus"] else f"{t['name']} is where the time is. ")
        laps = f"the last {t['laps']} laps" if t["laps"] > 1 else "that lap"
        cost = f"About {say_gap(t['loss_per_lap_s'])} a lap over {laps}."
        how = longer(t["name"], t["cause"], t["amount"], t.get("ref") or {}, t.get("mine") or {}, named=False)
        lines.append(f"{lead}{cost}" + (f" {how}" if how else ""))
    pace = consistency(lap_times)
    if not lines:
        tidy = f"{opener}no one corner stands out on those laps."
        return [f"{tidy} {pace}" if pace else f"{tidy} Good work."]
    if pace:
        lines.append(pace)
    return lines


def consistency(lap_times: list[float]) -> str | None:
    times = lap_times[-3:]
    if len(times) < 3:
        return None
    spread = max(times) - min(times)
    if spread <= 0.3:
        return f"Last three laps within {say_gap(spread)} of each other. Nice and consistent."
    if spread <= 0.6:
        return f"Last three laps within {say_gap(spread)}. Fairly consistent, a bit more to tidy up."
    return (f"Last three laps varied by {say_gap(spread)}. Get the same lap every time first, "
            "then go looking for more.")
