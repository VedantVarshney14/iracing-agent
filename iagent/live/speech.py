"""Who speaks when: the speech arbiter and the voices it drives.

The arbiter owns the audio: one utterance at a time, the most important first, a short quiet gap
between them, nothing said after it has expired (a corner cue is useless once the corner has
gone by), and nothing new while a car is alongside (CrewChief's spotter is talking then). Time is
the session's clock, so a replay at any speed makes the same decisions as the live session.

Like an engineer on the radio, how much is said depends on the moment: a line can carry a longer
version (the why and the how, not just the what), said instead when the driver isn't pushing and
there's room before the next corner; on a push lap only the short one is.
"""

import sys
from dataclasses import dataclass, field
from typing import Callable, Protocol

# Lower is more important.
APPROACH = 0  # a cue for the corner ahead: time-critical
FEEDBACK = 1  # how the last corner went
ANSWER = 1  # a reply to the driver
SUMMARY = 2  # end of lap

CHARS_PER_S = 14.0  # Pocket TTS speaks ~14 characters a second


def estimate_duration(text: str) -> float:
    return len(text) / CHARS_PER_S + 0.25


@dataclass
class Utterance:
    text: str
    priority: int
    kind: str  # "approach", "feedback", "summary", "focus", "answer", "rule"
    created_s: float
    expires_s: float  # session time after which it is no longer worth starting
    corner: int | None = None
    duration_s: float | None = None  # known for pre-rendered audio; estimated otherwise
    note: str | None = None  # why it's still waiting (reported if it's dropped)
    rule: str | None = None  # the rule that said it, if one did
    longer: str | None = None  # a fuller version, for when the driver has the time to listen

    @property
    def length_s(self) -> float:
        return self.duration_s if self.duration_s is not None else estimate_duration(self.text)


class Voice(Protocol):
    def duration(self, text: str) -> float | None:
        """Exact length when the audio is already rendered, else None."""

    def play(self, utterance: Utterance, now: float) -> None:
        """Start speaking; returns at once (audio plays in the background)."""

    def stop(self) -> None:
        """Cut off whatever is being said (a corner cue can't wait for a lap summary)."""


@dataclass
class Spoken:
    at_s: float
    text: str
    kind: str
    corner: int | None
    cut: bool = False  # interrupted by something more urgent


@dataclass
class CapturedVoice:
    """Records what would have been said and when (tests, backtests, dry runs)."""

    spoken: list[Spoken] = field(default_factory=list)

    def duration(self, text: str) -> float | None:
        return None

    def play(self, utterance: Utterance, now: float) -> None:
        self.spoken.append(Spoken(now, utterance.text, utterance.kind, utterance.corner))

    def stop(self) -> None:
        if self.spoken:
            self.spoken[-1].cut = True


class PrintVoice(CapturedVoice):
    """Prints each line with the session time: a voice for the terminal."""

    def play(self, utterance: Utterance, now: float) -> None:
        super().play(utterance, now)
        minutes, seconds = divmod(round(now, 2), 60)
        print(f"[{int(minutes):02d}:{seconds:05.2f}] {utterance.kind:<8} {utterance.text}", file=sys.stdout, flush=True)


# What happened to a line: said, cut (interrupted), dropped (expired unsaid), cleared.
Event = Callable[[str, Utterance, float], None]


class Arbiter:
    def __init__(self, voice: Voice, quiet_gap_s: float = 0.6, on_event: Event | None = None):
        self.voice = voice
        self.quiet_gap_s = quiet_gap_s
        self.on_event = on_event
        self._queue: list[Utterance] = []
        self._busy_until = -1.0
        self._current: Utterance | None = None
        self.dropped: list[Utterance] = []

    @property
    def queue(self) -> list[Utterance]:
        return list(self._queue)

    def say(self, utterance: Utterance) -> None:
        if utterance.duration_s is None:
            utterance.duration_s = self.voice.duration(utterance.text)
        self._queue.append(utterance)

    def speaking(self, now: float) -> bool:
        return now < self._busy_until

    def tick(self, now: float, hold: bool = False, free_for_s: float = float("inf"),
             long_ok: bool = False, hold_why: str = "a car was alongside") -> Utterance | None:
        """Start the next utterance if the channel is free.

        Args:
            hold: say nothing new now (a car alongside); queued items wait until they expire.
            free_for_s: how long until a time-critical cue is due: anything less important that
                wouldn't finish by then waits.
            long_ok: the driver can take a longer line (not pushing): a line's `longer` version
                is said instead when it fits before the next cue.
        """
        for u in [u for u in self._queue if u.expires_s < now]:
            self._queue.remove(u)
            self.dropped.append(u)
            self._emit("dropped", u, now)
        if not self._queue:
            return None
        if hold:
            self._note(hold_why)
            return None
        if self.speaking(now):
            urgent = any(u.priority == APPROACH for u in self._queue)
            if not (urgent and self._current is not None and self._current.priority > APPROACH):
                self._note("something else was being said")
                return None
            self.voice.stop()  # a corner cue cuts off feedback or a summary
            self._emit("cut", self._current, now)
            self._busy_until = now
        for u in sorted(self._queue, key=lambda u: (u.priority, u.created_s)):
            if u.priority > APPROACH and u.length_s > free_for_s:
                u.note = "in a corner" if free_for_s <= 0 else "no straight long enough before the next corner"
                continue
            self._queue.remove(u)
            if long_ok and u.longer and u.priority > APPROACH:
                longer = self.voice.duration(u.longer) or estimate_duration(u.longer)
                if longer <= free_for_s:
                    u.text, u.duration_s, u.longer = u.longer, longer, None
            self.voice.play(u, now)
            self._current = u
            self._busy_until = now + u.length_s + self.quiet_gap_s
            self._emit("said", u, now)
            return u
        return None

    def clear(self, kinds: tuple[str, ...] | None = None, now: float = 0.0, why: str | None = None) -> None:
        keep = [u for u in self._queue if kinds is not None and u.kind not in kinds]
        for u in self._queue:
            if u not in keep:
                u.note = why or u.note
                self._emit("dropped", u, now)
        self._queue = keep

    def _note(self, why: str) -> None:
        for u in self._queue:
            u.note = why

    def _emit(self, what: str, u: Utterance, now: float) -> None:
        if self.on_event is not None:
            self.on_event(what, u, now)
