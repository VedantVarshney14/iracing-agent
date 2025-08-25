import asyncio
import logging
import os

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

import iagent
from agent import DriverMessage, AgentState, get_initial_state, Agent
from audio.tts import TTS
from iagent import utils
from iagent.mcp_servers import garage_mcp
from mcp_servers import iracing_mcp

logger = logging.getLogger(__name__)


async def main():
    logger.info("Setting up TTS")
    tts = TTS(
        phonetics={
            "Vedant": "/ˈvɪdænt/"
        }
    )
    logger.info("Setting up tools.")
    client = MultiServerMCPClient(
        {
            "iracing": {
                "command": "python",
                "args": [iracing_mcp.__file__],
                "transport": "stdio",
            },
            "garage": {
                "command": "python",
                "args": [garage_mcp.__file__, os.environ["GARAGE61_PAT"]],
                "transport": "stdio",
            }
        }
    )

    async with (
        client.session("iracing") as iracing_session,
        client.session("garage") as garage_session
    ):
        tools = await load_mcp_tools(iracing_session)
        tools += await load_mcp_tools(garage_session)
        agent = Agent(tools)

        # Initial state
        state = get_initial_state()
        state["trigger"] = "driver"

        default_msg = "Hey - how was my last lap?"

        while True:
            # TODO - replace with audio parsing
            msg = input("Driver message ([Enter] to use the default: ")
            if not msg:
                msg = default_msg
            logger.info(f"Driver Message: {msg}")
            state["driver_messages"].append(
                HumanMessage(msg)
            )
            state["race_engineer_messages"].append(
                DriverMessage(msg)
            )
            state: AgentState = await agent.graph.ainvoke(state)
            if state["send_driver_message"]:
                ai_msg = state['driver_messages'][-1].content
                logger.info(f"AI Message: {ai_msg}")
                tts.generate(ai_msg)
                state["send_driver_message"] = False


if __name__ == '__main__':
    utils.setup_logger(iagent.__name__)
    utils.setup_logger(__name__)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    resp = loop.run_until_complete(main())
