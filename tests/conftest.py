import pytest

from iagent.testing.synthetic import LapKind, SyntheticSource


@pytest.fixture
def clean_source() -> SyntheticSource:
    return SyntheticSource(n_laps=4, seed=1)


@pytest.fixture
def messy_source() -> SyntheticSource:
    """A partial first lap, then every kind of flagged lap, then a clean lap."""
    return SyntheticSource(
        n_laps=6,
        kinds=[
            LapKind.CLEAN,  # partial: session starts mid-lap
            LapKind.OFF_TRACK,
            LapKind.PIT_IN,
            LapKind.OUT_LAP,
            LapKind.RESET,
            LapKind.CLEAN,
        ],
        start_m=1200.0,
        seed=2,
    )


@pytest.fixture(autouse=True)
def no_audio(monkeypatch):
    """Tests never play sound: opening an audio device fails the test."""
    try:
        import sounddevice
    except (ImportError, OSError):  # the voice extra isn't installed: nothing could play
        return

    def refuse(*args, **kwargs):
        raise AssertionError("A test tried to play audio; use CapturedVoice instead.")

    for name in ("play", "OutputStream", "RawOutputStream", "Stream", "RawStream"):
        monkeypatch.setattr(sounddevice, name, refuse)
