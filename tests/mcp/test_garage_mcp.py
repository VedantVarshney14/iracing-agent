import fastmcp
import pytest


@pytest.fixture(scope="module")
def client():
    from iagent.mcp_servers.garage_mcp import mcp
    return fastmcp.Client(mcp)


@pytest.mark.skip("TODO - debugging test using live environment (not a unit test) - remove")
@pytest.mark.asyncio
async def test_analyse_last_lap(client):
    async with client:
        result = await client.call_tool(
            "analyse_last_lap"
        )
