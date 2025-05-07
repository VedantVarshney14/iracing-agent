import pytest

from iagent.tools import IRacingTools


@pytest.fixture()
def itools(ir):
    return IRacingTools(ir)


def test_build_tools(itools):
    built_tools = itools.build_tools()
    telemetry_tool = built_tools["get_current_telemetry_data"]

    assert telemetry_tool.name == "get_current_telemetry_data"
    assert telemetry_tool.description.startswith("Get current iRacing telemetry")

    data = telemetry_tool.invoke({"key": "Speed"})
    assert isinstance(data, float)

    telemetry_key_tool = built_tools["telemetry_key_lookup"]
    data = telemetry_key_tool.invoke(
        {"text": "Air Temperature", "k": 5}
    )
    assert isinstance(data, list)
    assert data[0] == {"AirDensity": "Density of air at start/finish line, kg/m^3"}
