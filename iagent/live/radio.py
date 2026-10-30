"""The sound of a team radio: a short crackle as someone keys the mic, and another as they let it
go.

Made from noise, not a recording: broken into bursts of a few milliseconds with a few clicks, and
the whole of it kept low (roughly 150-1200 Hz) so it's a rumble, not a hiss. The engineer's lines
go out between the two (`iagent.live.speech`), and push-to-talk crackles as the driver presses and
releases the button (`Keyed`, played by `iagent.live.push_to_talk`).
"""

import threading

import numpy as np

KEY_UP_S = 0.15  # the crackle before a transmission
KEY_DOWN_S = 0.10  # and after it
LEVEL = 0.3  # peak, against speech at up to 1.0
LOW_HZ, HIGH_HZ = 150, 1200  # the band it's kept to


def crackle(
    sample_rate: int, seconds: float, level: float = LEVEL, seed: int = 0
) -> np.ndarray:
    """SECONDS of radio crackle at SAMPLE_RATE (mono float32, peak LEVEL), fading out; the same
    SEED gives the same crackle."""
    rng = np.random.default_rng(seed)
    n = max(int(seconds * sample_rate), 1)
    burst = max(int(0.004 * sample_rate), 1)  # it comes and goes every few ms
    steps = n // burst + 1
    gate = np.repeat((rng.random(steps) < 0.6) * rng.uniform(0.3, 1.0, steps), burst)
    clicks = (rng.random(n) < 60 / sample_rate) * rng.choice([-3.0, 3.0], n)
    x = rng.standard_normal(n) * gate[:n] + clicks
    x -= _smooth(x, sample_rate // LOW_HZ)  # drop the rumble below the band...
    x = _smooth(x, sample_rate // HIGH_HZ)  # ...and the hiss above it
    attack = min(int(0.005 * sample_rate), n)
    envelope = np.ones(n)
    envelope[:attack] = np.linspace(0.0, 1.0, attack)
    # fades out over its second half
    envelope[n // 2 :] = np.linspace(1.0, 0.0, n - n // 2)
    x *= envelope
    peak = np.abs(x).max()
    return (x * (level / peak if peak else 0.0)).astype(np.float32)


def key_up(sample_rate: int) -> np.ndarray:
    return crackle(sample_rate, KEY_UP_S, seed=1)


def key_down(sample_rate: int) -> np.ndarray:
    return crackle(sample_rate, KEY_DOWN_S, seed=2)


class Keyed:
    """The crackles of the driver's own transmission: the key-up on `press`, the key-down on
    `release`. `fill` is called from an audio callback, the others from anywhere (they only swap
    what's to play, so the keyboard hook never waits on audio)."""

    def __init__(self, sample_rate: int):
        self._up, self._down = key_up(sample_rate), key_down(sample_rate)
        self._tail = np.zeros(0, np.float32)  # what's still to play
        self._lock = threading.Lock()

    def press(self) -> None:
        with self._lock:
            self._tail = self._up

    def release(self) -> None:
        with self._lock:
            self._tail = self._down

    def fill(self, n: int) -> np.ndarray:
        """The next N samples to play: silence once the crackle is done."""
        out = np.zeros(n, np.float32)
        with self._lock:
            k = min(n, len(self._tail))
            out[:k] = self._tail[:k]
            self._tail = self._tail[k:]
        return out


def _smooth(x: np.ndarray, width: int) -> np.ndarray:
    """X through a moving average WIDTH samples wide (a crude low-pass)."""
    if width <= 1:
        return x
    return np.convolve(x, np.ones(width) / width, mode="same")
