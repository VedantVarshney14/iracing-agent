import pytest

from iagent.audio.tts import TTS


@pytest.fixture(scope="module")
def tts():
    return TTS()

def test_tts(tts):
    _ = tts.generate("Your oil temps are at 77 degrees. They're normal, keep pushing!")