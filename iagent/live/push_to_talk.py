"""Push-to-talk: hold a key, speak, and the engineer gets the words as a question.

On the sim PC the key is a wheel button: SimHub's controls mapping turns the button into a
keystroke, and this listens for that key anywhere (iRacing has the focus). The microphone is
open all the time but only kept while the key is held; on release the audio is transcribed on
another thread (Moonshine, on the CPU) and the text goes to `on_text`, which asks the engineer.
Pressing and releasing crackle like a radio being keyed (`radio.Keyed`), so the driver hears the
mic go live and go off.
"""

import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from iagent.live import radio

logger = logging.getLogger("iagent.live")

SAMPLE_RATE = 16_000  # what Moonshine works at, so nothing is resampled
MIN_S = 0.4  # a shorter press was a knock on the button, not a question
MAX_S = 30.0  # holding longer than this keeps the first 30 s

Transcribe = Callable[
    [np.ndarray], str
]  # mono float32 audio at SAMPLE_RATE -> the words


def moonshine(cache: Path) -> Transcribe:
    """Moonshine's small English model (downloaded into CACHE on first use) as a `Transcribe`."""
    try:
        import moonshine_voice
    except ImportError as e:
        raise RuntimeError(
            "Push-to-talk needs the voice extra: uv sync --extra voice"
        ) from e
    arch = moonshine_voice.ModelArch.SMALL_STREAMING
    path, arch = moonshine_voice.get_model_for_language("en", arch, cache_root=cache)
    transcriber = moonshine_voice.Transcriber(model_path=path, model_arch=arch)
    lock = threading.Lock()  # one transcript at a time

    def transcribe(audio: np.ndarray) -> str:
        with lock:
            lines = transcriber.transcribe_without_streaming(
                audio.tolist(), SAMPLE_RATE
            ).lines
        return " ".join(line.text.strip() for line in lines).strip()

    return transcribe


def parse_key(name: str):
    """The pynput key for NAME: a single character ("t") or a special key's name ("f9",
    "scroll_lock", "pause"). Anything else is an error naming the options."""
    from pynput import keyboard

    if name in keyboard.Key.__members__:
        return keyboard.Key[name]
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(
        f"Unknown push-to-talk key {name!r}: use one character or a key name such as "
        "f9, scroll_lock or pause"
    )


class PushToTalk:
    """Listens for the key and the microphone. While the key is held, audio is kept; on release
    it is transcribed and the words go to ON_TEXT (on the transcribing thread, never on the
    coach's)."""

    def __init__(
        self, key: str, on_text: Callable[[str], None], transcribe: Transcribe
    ):
        self._key = parse_key(key)
        self.radio = radio.Keyed(SAMPLE_RATE)
        self._on_text = on_text
        self._transcribe = transcribe
        self._chunks: list[np.ndarray] = []
        self._held = False
        self._stream = None
        self._out = None
        self._listener = None

    def start(self) -> None:
        """Open the microphone and start listening for the key."""
        import sounddevice
        from pynput import keyboard

        self._stream = sounddevice.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=lambda data, *_: self.hear(data[:, 0]),
        )
        self._stream.start()
        self._out = sounddevice.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._play
        )
        self._out.start()
        # The callbacks return None: pynput stops listening when one returns False.
        self._listener = keyboard.Listener(
            on_press=self._pressed, on_release=self._released
        )
        self._listener.start()

    def close(self) -> None:
        """Stop listening and close the microphone."""
        if self._listener is not None:
            self._listener.stop()
        if self._stream is not None:
            self._stream.close()
        if self._out is not None:
            self._out.close()

    def _play(self, out, frames, *_) -> None:
        """The output stream's callback: a crackle after a press or release, else silence."""
        out[:, 0] = self.radio.fill(frames)

    def _pressed(self, key) -> None:
        if key == self._key:
            self.press()

    def _released(self, key) -> None:
        if key == self._key:
            self.release()

    def press(self) -> None:
        """The key went down (key repeat while held changes nothing): crackle, and start keeping
        audio."""
        if not self._held:
            self._chunks, self._held = [], True
            self.radio.press()

    def hear(self, chunk: np.ndarray) -> None:
        """Audio from the microphone: kept while the key is held, up to MAX_S."""
        if self._held and sum(len(c) for c in self._chunks) < MAX_S * SAMPLE_RATE:
            self._chunks.append(chunk.copy())

    def release(self) -> None:
        """The key came up: transcribe what was said, unless it was too short to be a question."""
        if not self._held:
            return
        self._held = False
        self.radio.release()
        audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, "float32")
        self._chunks = []
        if len(audio) >= MIN_S * SAMPLE_RATE:
            threading.Thread(
                target=self._ask, args=(audio,), name="push-to-talk", daemon=True
            ).start()

    def _ask(self, audio: np.ndarray) -> None:
        """Transcribe AUDIO and pass the words on, if there were any."""
        try:
            text = self._transcribe(audio)
        except Exception:
            logger.exception("Push-to-talk: transcription failed")
            return
        if text:
            self._on_text(text)


def listen(key: str, cache: Path, on_text: Callable[[str], None]) -> PushToTalk:
    """Push-to-talk on KEY, started: loads the speech model (into CACHE) and opens the microphone.
    A bad KEY or a missing voice extra is an error before anything loads."""
    parse_key(key)
    talk = PushToTalk(key, on_text, moonshine(cache))
    talk.start()
    return talk
