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
