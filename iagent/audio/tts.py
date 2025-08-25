import logging
from typing import Optional

import sounddevice as sd
import torch
from kokoro import KPipeline

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000


class TTS:
    def __init__(
            self,
            lang_code: str = "b",
            repo_id: str = "hexgrad/Kokoro-82M",
            phonetics: Optional[dict[str, str]] = None
    ):
        self._pipeline = KPipeline(repo_id=repo_id, lang_code=lang_code)
        self._phonetics = phonetics

    def generate(self, text: str, play_audio: bool = True, voice: str = "af_heart") -> Optional[torch.Tensor]:
        """
        Generate audio from text.

        Parameters
        ----------
        text : str
            Text to generate audio for.
        play_audio : bool
            Play audio and block until completion. If False, return audio array (flattened).
        voice : str

        Returns
        -------
        Optional[torch.Tensor]
            Audio array or None if audio was played.
        """
        if self._phonetics:
            for word, phonetic in self._phonetics.items():
                text = text.replace(word, f"{word} ({phonetic})")
        generator = self._pipeline(text, voice=voice)
        for (_gs, _ps, audio) in generator:
            if play_audio:
                sd.play(audio, samplerate=SAMPLE_RATE)
                sd.wait()
                return None
            return audio
        return None
