import pytest

from iagent.laps.recorder import record
from iagent.laps.store import ParquetLapStore
from iagent.testing.synthetic import SyntheticSource


@pytest.fixture
def root(tmp_path):
    """A workspace holding four synthetic laps (the first a partial one), so a coach can be built."""
    store = ParquetLapStore(tmp_path / "ws")
    record(SyntheticSource(n_laps=4, seed=1), store, "synthetic")
    store.close()
    return tmp_path / "ws"
