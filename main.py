import asyncio
import logging
import os

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

import iagent
from iagent.mcp_servers import garage_mcp
from iagent import utils

logger = logging.getLogger(__name__)


LLM_PROMPT = (
    "You are sim-racing coach named Steve. Your job is to coach "
    "Vedant (the user) improve at the racing simulator iRacing. You will "
    "have access to his session data. Reply to Vedant's "
    "questions and advise him so he can be the best racing "
    "driver possible! Remember to keep your answers short "
    "(as though you are speaking over the radio while he is "
    "in the car). "
    "Your final *non-thinking* response will be relayed to Vedant via radio."
)


DEBUG = True

async def main():
    logger.info("Setting up tools.")
    client = MultiServerMCPClient(
        {
            "garage": {
                "command": "python",
                "args": [garage_mcp.__file__, os.environ["GARAGE61_PAT"]],
                "transport": "stdio",
            }
        }
    )

    async with client.session("garage") as session:
        built_tools = await load_mcp_tools(session)

        model = ChatOllama(
            model="qwen3:4b",
            temperature=0
        )

        agent = create_react_agent(
            model,
            built_tools,
            prompt=LLM_PROMPT,
            debug=True
        )

        messages = await agent.ainvoke(
            {
                "messages": [
                    # ("human", "Analyse my last lap?"),
                    ("human", "I'm understeering too much in T1. Any tips?")
                ]
            }
        )

        return messages



if __name__ == '__main__':
    utils.setup_logger(iagent.__name__)
    utils.setup_logger(__name__)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    resp = loop.run_until_complete(main())
