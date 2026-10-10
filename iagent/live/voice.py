"""Speech through Pocket TTS (Kyutai, ~100M parameters, CPU, ~2 threads).

Cue texts known before the session are rendered to WAV files up front (cached by voice and
text), so playing one costs nothing and its exact length is known when timing the cue. Anything
else (feedback, summaries, later answers) is streamed: first audio after ~50-200 ms.

Optional dependency: `uv sync --extra voice` (pocket-tts, which brings PyTorch, and sounddevice).
"""

import hashlib
import logging
import queue
import threading
import wave
from pathlib import Path
from typing import Iterable

import numpy as np

from iagent.live.speech import Utterance

logger = logging.getLogger("iagent.voice")

DEFAULT_VOICE = "alba"
BLOCK = 2048  # frames per write: big blocks and a high-latency stream ride out a busy coach loop
PRIME_S = 0.3  # streamed speech: buffer this much before starting, so the stream never runs dry


class PocketVoice:
    def __init__(self, cache_dir: Path, voice: str = DEFAULT_VOICE, threads: int = 2, device: int | str | None = None):
        try:
            import sounddevice
            import torch
            from pocket_tts import TTSModel
        except ImportError as e:
            raise RuntimeError("Speech needs the voice extra: `uv sync --extra voice`.") from e
        torch.set_num_threads(threads)  # leave the rest of the CPU to the sim
        self._sd = sounddevice
        self._device = device
        self._model = TTSModel.load_model()
        self._voice = self._model.get_state_for_audio_prompt(voice)
        self.sample_rate = self._model.sample_rate
        self._cache = cache_dir / _safe(voice)
        self._cache.mkdir(parents=True, exist_ok=True)
        self._voice_name = voice
        self._durations: dict[str, float] = {}
        self.underruns = 0  # audio blocks that arrived late (heard as crackles)
        self._cut = threading.Event()
        self._jobs: queue.Queue[str | None] = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="voice", daemon=True)
        self._worker.start()

    # --- the Voice protocol ----------------------------------------------------------------

    def duration(self, text: str) -> float | None:
        if text in self._durations:
            return self._durations[text]
        path = self._path(text)
        if path.exists():
            with wave.open(str(path)) as w:
                self._durations[text] = w.getnframes() / w.getframerate()
            return self._durations[text]
        return None

    def play(self, utterance: Utterance, now: float) -> None:
        self._jobs.put(utterance.text)

    def stop(self) -> None:
        self._cut.set()

    # --- rendering and playback --------------------------------------------------------------

    def prepare(self, texts: Iterable[str]) -> int:
        """Render texts that aren't cached yet; returns how many were rendered."""
        n = 0
        for text in dict.fromkeys(texts):
            if self.duration(text) is None:
                audio = self._model.generate_audio(self._voice, text).numpy()
                self._write(self._path(text), audio)
                self._durations[text] = len(audio) / self.sample_rate
                n += 1
        return n

    def close(self) -> None:
        self._jobs.put(None)
        self._worker.join(timeout=10)
        if self.underruns:
            logger.warning("%d audio underrun(s): speech may have crackled", self.underruns)

    def _run(self) -> None:
        while (text := self._jobs.get()) is not None:
            self._cut.clear()
            try:
                path = self._path(text)
                if path.exists():
                    self._write_out([_read(path)])
                else:
                    self._write_out(chunk.numpy() for chunk in self._model.generate_audio_stream(self._voice, text))
            except Exception:  # never take the coach down over audio
                logger.exception("Couldn't speak %r", text)

    def _write_out(self, chunks: Iterable[np.ndarray]) -> None:
        """Play audio with blocking writes from this thread (no Python callback to starve)."""
        pending = np.zeros(0, dtype=np.float32)
        prime = int(PRIME_S * self.sample_rate)
        started = False
        with self._sd.OutputStream(samplerate=self.sample_rate, channels=1, dtype="float32", device=self._device,
                                   blocksize=BLOCK, latency="high") as out:
            for chunk in chunks:
                if self._cut.is_set():
                    return
                pending = np.concatenate([pending, np.asarray(chunk, dtype=np.float32).reshape(-1)])
                if not started and len(pending) < prime:
                    continue
                started = True
                while len(pending) >= BLOCK:
                    if self._cut.is_set():
                        return
                    self._block(out, pending[:BLOCK])
                    pending = pending[BLOCK:]
            pending = np.concatenate([pending, np.zeros(BLOCK - len(pending) % BLOCK, dtype=np.float32)])
            for i in range(0, len(pending), BLOCK):
                if self._cut.is_set():
                    return
                self._block(out, pending[i : i + BLOCK])
            out.write(np.zeros((BLOCK * 2, 1), dtype=np.float32))  # let the last block drain before closing

    def _block(self, out, block: np.ndarray) -> None:
        if out.write(block.reshape(-1, 1)):
            self.underruns += 1

    def _path(self, text: str) -> Path:
        return self._cache / (hashlib.sha1(text.encode()).hexdigest()[:16] + ".wav")

    def _write(self, path: Path, audio: np.ndarray) -> None:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm.tobytes())


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32767


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in Path(name).stem)
