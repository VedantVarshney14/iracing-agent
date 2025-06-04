
import pytest
import matplotlib as mpl

from iagent.garage import plot
from iagent.garage.garage_client import GarageClient
from iagent.garage.models import Lap

@pytest.fixture(scope="module")
def client():
    return GarageClient()

@pytest.mark.asyncio
async def test_get_laps(client):
    lap = await client.get_user_lap()
    assert isinstance(lap, Lap)

@pytest.mark.asyncio
async def test_get_laps(client):
    lap = await client.get_user_lap()
    telem = await client.get_lap_telemetry(lap.id)
    fig = plot.plot_lap(lap, telem)
    mpl.use("TkAgg")  # Use a non-interactive backend for testing
    pass