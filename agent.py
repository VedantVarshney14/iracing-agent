import asyncio
import logging
import os

from jedi.inference.gradual.typing import TypedDict
from langchain_core.messages import HumanMessage, SystemMessage, AnyMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_ollama import ChatOllama
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from langgraph.constants import END, START
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition

import iagent
from iagent import utils
from iagent.mcp import garage_mcp, iracing_mcp

logger = logging.getLogger(__name__)

DEFAULT_LLM_PROMPT = (
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

class State(MessagesState):
    pass


async def main():
    langfuse = Langfuse()

    logger.info("Setting up tools.")
    client = MultiServerMCPClient(
        {
            "garage": {
                "command": "python",
                "args": [garage_mcp.__file__, os.environ["GARAGE61_PAT"]],
                "transport": "stdio",
            },
            "iracing": {
                "command": "python",
                "args": [iracing_mcp.__file__],
                "transport": "stdio",
            },
        }
    )

    async with (client.session("garage") as session):
        tools = await load_mcp_tools(session)

        model = ChatOllama(
            model="qwen3:4b",
            temperature=0
        )

        model_with_tools = model.bind_tools(tools)

        async def call_model(_state: State):
            response_message = await model_with_tools.ainvoke(_state["messages"])
            return {"messages": response_message}

        builder = StateGraph(State)
        builder.add_node("agent", call_model)
        builder.add_node("tools", ToolNode(tools))
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", tools_condition, ["tools", END])
        builder.add_edge("tools", "agent")

        graph = builder.compile()

        default_question = (
            "How was my last lap?"
        )

        langfuse_handler = CallbackHandler()

        base_prompt = langfuse.get_prompt(
            "Agent-Base-Prompt",
            fallback=DEFAULT_LLM_PROMPT
        )

        state: State = {
            "messages": [
                SystemMessage(content=base_prompt.get_langchain_prompt())
            ]
        }

        while True:
            try:
                question = input(
                    "Enter your question (press [enter] for default, 'exit' to exit.): "
                )
                if question.lower() == "exit":
                    break
                if not question:
                    question = default_question
                state["messages"].append(
                    HumanMessage(content=question)
                )
                logger.info("Invoking agent...")
                result = await graph.ainvoke(
                    state,
                    config={
                        "callbacks": [langfuse_handler]
                    }
                )

                reply = result["messages"][-1]
                state["messages"].append(reply)
                # TODO - Trim state?
                print(result["messages"][-1].content)
            except KeyboardInterrupt:
                break



if __name__ == '__main__':
    utils.setup_logger(iagent.__name__)
    utils.setup_logger(__name__)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    resp = loop.run_until_complete(main())
