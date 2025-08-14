"""
An MCP server for iRacing telemetry analysis.
"""
import functools
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional, Any

import fastmcp
import irsdk

from iagent import utils
from iagent.tools import IRacingTools
from iagent.vision import VisionModel


@dataclass
class State:
    client: IRacingTools
    vision: VisionModel


DEBUG = True  # Set to False in production


@asynccontextmanager
async def lifespan(_app):
    """Context manager for MCP server lifecycle."""
    ir = irsdk.IRSDK()
    if DEBUG:
        ir.startup(
            test_file=str(utils.get_misc_data_path() / "data.bin")
        )
    else:
        ir.startup()

    state = State(
        client=IRacingTools(ir),
        vision=VisionModel(),
    )
    _app.state = state
    yield
    ir.shutdown()


mcp = fastmcp.FastMCP(
    "iRacing MCP Server 🚗",
    lifespan=lifespan
)


@mcp.tool()
@functools.wraps(IRacingTools.get_current_telemetry_data)
def get_current_telemetry_data(ctx: fastmcp.Context, key: str) -> Optional[Any]:
    state: State = ctx.fastmcp.state
    return state.client.get_current_telemetry_data(key)


if __name__ == '__main__':
    mcp.run()
