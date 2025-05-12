import logging
from typing import Optional

import numpy as np
import sounddevice as sd
from dia import Dia
from dia.model import DEFAULT_SAMPLE_RATE
from torch.cuda import is_available

logger = logging.getLogger(__name__)

TTS_DESCRIPTION = (
    "A male speaker delivers a slightly expressive and animated speech with a "
    "moderate speed and pitch. The recording is of very high quality, with the "
    "speaker's voice sounding clear and very close up."
)


class TTS:
    def __init__(
            self,
            device: Optional[str] = None,
            model: str = "nari-labs/Dia-1.6B"
    ):
        if device is None:
            device = "cuda:0" if is_available() else "cpu"
        self._device = device
        logger.info(
            "Setting up TTS models..."
        )
        # TODO - configure device
        self._model = (
            Dia.from_pretrained(model, compute_dtype="float16")
        )

    def generate(self, text: str, play_audio: bool = True) -> Optional[np.ndarray]:
        """
        Generate audio from text.

        Parameters
        ----------
        text : str
            Text to generate audio for.
        play_audio : bool
            Play audio and block until completion. If False, return audio array (flattened).

        Returns
        -------
        Optional[np.ndarray]
            Audio array or None if audio was played.
        """
        audio = self._model.generate(
            text=f"[S1] {text}",
            use_torch_compile=False,
            verbose=True
        )
        if play_audio:
            sd.play(audio, DEFAULT_SAMPLE_RATE)
            sd.wait()
            return None
        return audio
