from pathlib import Path

import irsdk
import pytest

from iagent.tools import IRacingTools


@pytest.fixture(scope="session")
def ir():
    ir = irsdk.IRSDK()
    data_path = Path(__file__).parent / "data" / "iracing-telemetry.bin"
    assert data_path.is_file()
    ir.startup(
        test_file=data_path
    )
    return ir

@pytest.fixture()
def itools(ir):
    return IRacingTools(ir)

def test_get_current_telemetry_data(itools):
    built_tools = itools.build_tools()
    data = built_tools["get_current_telemetry_data"].invoke({})
    assert isinstance(data, dict)
    assert "Speed" in data
    assert isinstance(data["Speed"], float)