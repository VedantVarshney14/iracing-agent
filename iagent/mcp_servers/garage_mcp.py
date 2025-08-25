"""
An MCP server for interacting with the iRacing Garage61 platform.
"""
import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass

import fastmcp
import ollama

from iagent import utils
from iagent.garage import plot
from iagent.garage.garage_client import GarageClient
from iagent.vision import VisionModel


@dataclass
class State:
    client: GarageClient
    vision: VisionModel


@asynccontextmanager
async def lifespan(_app):
    """Context manager for MCP server lifecycle."""
    state = State(
        client=GarageClient(),
        vision=VisionModel(model_name="gemma3:4b"),
    )
    _app.state = state
    yield


mcp = fastmcp.FastMCP(
    "Garage61 MCP Server 🚗",
    lifespan=lifespan
)


@mcp.resource("resource://setup-guide")
async def get_setup_guide():
    """Returns a guide on common car setup adjustment tips."""
    with open(utils.get_data_path() / "driver-61-setup-guide.txt", "r") as file:
        guide = file.read()
    return guide


@mcp.tool()
async def get_lap_details(ctx: fastmcp.Context) -> dict:
    """
    Get details about the last user lap. Details include track name, lap time, car name, track temperature etc.
    """
    state: State = ctx.fastmcp.state
    lap = await state.client.get_user_lap()
    return lap.model_dump()


@mcp.tool()
async def analyse_last_lap(
        ctx: fastmcp.Context,
) -> str:
    """Analyses the telemetry data of the last lap driven in iRacing and provides insights."""
    state: State = ctx.fastmcp.state
    lap = await state.client.get_user_lap()
    telemetry = await state.client.get_lap_telemetry(lap.id)
    telemetry_fig = plot.plot_lap_telemetry(
        lap, telemetry
    )
    messages = [
        ollama.Message(
            role="system",
            content="You are a helpful assistant that provides insights based on telemetry data from a single "
                    "lap driven in iRacing. "
                    "You should keep your responses concise, specific and relevant to the telemetry data provided. "
                    "Your responses will be relayed to the driver's engineer whose job will be to communicate your insights "
                    "to the driver via radio."
        ),
        state.vision.create_message(
            role="user",
            content=f"Please analyse the provided telemetry plot for the following lap: {lap.description()}",
            images=[telemetry_fig]
        )
    ]
    resp: ollama.ChatResponse = await state.vision.chat(
        messages=messages,
        stream=False
    )
    return resp.message.content


if __name__ == '__main__':
    if len(sys.argv) > 1:
        os.environ["GARAGE61_PAT"] = sys.argv[1]
    mcp.run(transport="stdio")
