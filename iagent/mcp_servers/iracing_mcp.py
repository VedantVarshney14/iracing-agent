"""
An MCP server for iRacing telemetry analysis.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from typing import Optional, Any

import fastmcp
import irsdk

from iagent import utils
from iagent.models import IRSDKVars
from iagent.tools import IRacingTools
from iagent.vision import VisionModel


@dataclass
class State:
    client: IRacingTools
    vision: VisionModel


@asynccontextmanager
async def lifespan(_app):
    """Context manager for MCP server lifecycle."""
    ir = irsdk.IRSDK()
    if utils.in_debug():
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
@utils.wrap_docs(IRacingTools.get_current_telemetry_data)
def get_current_telemetry_data(ctx: fastmcp.Context, key: str) -> Optional[Any]:
    state = ctx.fastmcp.state
    return {key: state.client.get_current_telemetry_data(key)}


# The telemetry keys resource (only exposed as an MCP resource, not a tool)
@mcp.resource("resource://telemetry-keys")
def get_telemetry_keys_resource() -> dict:
    """Resource: telemetry key names -> description.

    Returns a mapping of telemetry key string to human-readable description.
    """
    keys = {}
    for f in fields(IRSDKVars):
        desc = f.metadata.get("desc") if f.metadata else None
        keys[f.name] = desc
    return keys


if __name__ == '__main__':
    mcp.run()
