import pytest

from iagent.audio.tts import TTS


@pytest.fixture(scope="module")
def tts():
    return TTS()


@pytest.mark.skip("TODO - debugging test using live environment (not a unit test) - remove")
def test_tts(tts):
    _ = tts.generate("Your oil temps are at 77 degrees. They're normal, keep pushing!")
