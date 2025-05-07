from pathlib import Path

import irsdk
import pytest


@pytest.fixture(scope="session")
def ir():
    ir = irsdk.IRSDK()
    data_path = Path(__file__).parent / "data" / "iracing-telemetry.bin"
    assert data_path.is_file()
    ir.startup(
        test_file=data_path
    )
    return ir