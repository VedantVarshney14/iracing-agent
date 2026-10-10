"""Who speaks when: the speech arbiter and the voices it drives.

The arbiter owns the audio: one utterance at a time, the most important first, a short quiet gap
between them, nothing said after it has expired (a corner cue is useless once the corner has
gone by), and nothing new while a car is alongside (CrewChief's spotter is talking then). Time is
the session's clock, so a replay at any speed makes the same decisions as the live session.
"""

import sys
from dataclasses import dataclass, field
from typing import Protocol

# Lower is more important.
APPROACH = 0  # a cue for the corner ahead: time-critical
FEEDBACK = 1  # how the last corner went
SUMMARY = 2  # end of lap

CHARS_PER_S = 14.0  # Pocket TTS speaks ~14 characters a second


def estimate_duration(text: str) -> float:
    return len(text) / CHARS_PER_S + 0.25


@dataclass
class Utterance:
    text: str
    priority: int
    kind: str  # "approach", "feedback", "summary"
    created_s: float
    expires_s: float  # session time after which it is no longer worth starting
    corner: int | None = None
    duration_s: float | None = None  # known for pre-rendered audio; estimated otherwise

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
        minutes, seconds = divmod(now, 60)
        print(f"[{int(minutes):02d}:{seconds:05.2f}] {utterance.kind:<8} {utterance.text}", file=sys.stdout, flush=True)


class Arbiter:
    def __init__(self, voice: Voice, quiet_gap_s: float = 0.6):
        self.voice = voice
        self.quiet_gap_s = quiet_gap_s
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

    def tick(self, now: float, hold: bool = False, free_for_s: float = float("inf")) -> Utterance | None:
        """Start the next utterance if the channel is free.

        Args:
            hold: say nothing new now (a car alongside); queued items wait until they expire.
            free_for_s: how long until a time-critical cue is due: anything less important that
                wouldn't finish by then waits.
        """
        for u in [u for u in self._queue if u.expires_s < now]:
            self._queue.remove(u)
            self.dropped.append(u)
        if hold or not self._queue:
            return None
        if self.speaking(now):
            urgent = any(u.priority == APPROACH for u in self._queue)
            if not (urgent and self._current is not None and self._current.priority > APPROACH):
                return None
            self.voice.stop()  # a corner cue cuts off feedback or a summary
            self._busy_until = now
        for u in sorted(self._queue, key=lambda u: (u.priority, u.created_s)):
            if u.priority > APPROACH and u.length_s > free_for_s:
                continue
            self._queue.remove(u)
            self.voice.play(u, now)
            self._current = u
            self._busy_until = now + u.length_s + self.quiet_gap_s
            return u
        return None

    def clear(self, kinds: tuple[str, ...] | None = None) -> None:
        self._queue = [u for u in self._queue if kinds is not None and u.kind not in kinds]
