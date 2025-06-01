
import pytest

from iagent.garage.garage_client import GarageClient
from iagent.garage.models import Lap


@pytest.fixture(scope="module")
def client():
    return GarageClient()

@pytest.mark.asyncio
async def test_get_laps(client):
    lap = await client.get_user_lap()
    assert isinstance(lap, Lap)