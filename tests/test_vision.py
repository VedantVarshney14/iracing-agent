from pathlib import Path

import pytest
from iagent import vision

DATA_PATH = Path(__file__).parent / "data"

@pytest.fixture(scope="module")
def client():
    return vision.VisionModel(
        model_name="gemma3:12b"
    )

@pytest.mark.asyncio
async def test_create_message(client):
    messages = [
        client.create_message(
            role="user",
            content="Give me some tips based on this telemetry data of an F4 car going round Laguna Seca in iRacing.",
            images=[
                DATA_PATH / "telemetry_laguna_seca_f4.png"
            ]
        )
    ]