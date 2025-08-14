import asyncio
import logging
import os

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_ollama import ChatOllama
from langgraph.constants import END, START
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition
from langfuse.langchain import CallbackHandler

import iagent
from iagent import utils
from iagent.mcp import garage_mcp

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

    async with (client.session("garage") as session):
        tools = await load_mcp_tools(session)

        model = ChatOllama(
            model="qwen3:4b",
            temperature=0
        )

        model_with_tools = model.bind_tools(tools)

        async def call_model(state: MessagesState):
            response_message = await model_with_tools.ainvoke(state["messages"])
            return {"messages": response_message}

        builder = StateGraph(MessagesState)
        builder.add_node("agent", call_model)
        builder.add_node("tools", ToolNode(tools))
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", tools_condition, ["tools", END])
        builder.add_edge("tools", "agent")

        graph = builder.compile()

        mermaid = graph.get_graph().draw_mermaid()

        question = (
            "How was my last lap?"
        )

        langfuse_handler = CallbackHandler()

        result = await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": question}],
            },
            config={
                "callbacks": [langfuse_handler],
            }
        )

        print(result["messages"][-1].content)


if __name__ == '__main__':
    utils.setup_logger(iagent.__name__)
    utils.setup_logger(__name__)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    resp = loop.run_until_complete(main())
