import asyncio
import logging
import os
from json import JSONDecodeError
from typing import Optional, Literal, Any

from langchain_core.messages import HumanMessage, SystemMessage, AnyMessage, AIMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_ollama import ChatOllama
from langgraph import constants as lc
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

import iagent
from audio.tts import TTS
from iagent import utils
from iagent.events.events import EventStamp, EventPriority, Event
from iagent.mcp_servers import garage_mcp
from iagent.serialize import json
from mcp_servers import iracing_mcp

logger = logging.getLogger(__name__)


class DriverMessage(HumanMessage):
    def __init__(
            self, content: str, **kwargs: Any
    ) -> None:
        super().__init__(
            f"Driver Message:\n{content}",
            **kwargs
        )


class AssistantCoachMessage(HumanMessage):
    def __init__(
            self, content: str, **kwargs: Any
    ) -> None:
        super().__init__(
            f"Assistant Coach Message:\n{content}",
            **kwargs
        )


class AgentState(MessagesState):
    """State for the agentic system"""
    current_events: list[EventStamp]
    trigger: Optional[Literal["driver", "events"]]
    assistant_coach_messages: list[type[AnyMessage]]
    driver_messages: list[type[AnyMessage]]
    # Combination of assistant and driver messages - both useful to race engineer when considered
    # together
    race_engineer_messages: list[type[AnyMessage]]
    # Flag to output message to driver before ending graph
    send_driver_message: bool


def get_initial_state() -> AgentState:
    return {
        "current_events": [],
        "trigger": None,
        "assistant_coach_messages": [],
        "driver_messages": [],
        "race_engineer_messages": [],
        "send_driver_message": False,
    }


class Agent:
    def __init__(self, tools):
        self._model = ChatOllama(
            model="qwen3:8b"
            # model="gpt-oss:20b",
            # temperature=0
        ).bind_tools(tools)
        self.graph = self._build_graph(tools).compile()

    def _build_graph(self, tools) -> StateGraph:
        graph = StateGraph(AgentState)

        # Determine whether to start graph via a driver message or an event batch
        graph.add_conditional_edges(
            lc.START,
            self._trigger_check,
        )

        graph.add_node("race_engineer", self._race_engineer)
        graph.add_node(
            "race_engineer_tools",
            ToolNode(
                tools,
                name="race_engineer_tools",
                messages_key="race_engineer_messages"
            )
        )

        # Loop race engineer and tools until a non-tools final message is achieved
        graph.add_conditional_edges(
            "race_engineer",
            lambda state: self.contains_tool_calls(state["race_engineer_messages"][-1]),
            {
                True: "race_engineer_tools",
                False: lc.END
            }
        )
        graph.add_edge("race_engineer_tools", "race_engineer")

        graph.add_node("assistant_coach", self._assistant_coach)
        graph.add_node(
            "assistant_coach_tools",
            ToolNode(
                tools,
                name="assistant_coach_tools",
                messages_key="assistant_coach_messages"
            )
        )
        graph.add_conditional_edges(
            "assistant_coach",
            lambda state: self.contains_tool_calls(state["assistant_coach_messages"][-1]),
            {
                True: "assistant_coach_tools",
                False: "race_engineer",
            }
        )
        graph.add_edge("assistant_coach_tools", "assistant_coach")

        return graph

    @staticmethod
    async def _trigger_check(state: AgentState) -> Literal["race_engineer", "assistant_coach"]:
        if state["trigger"] == "driver":
            return "race_engineer"
        elif state["trigger"] == "events":
            return "assistant_coach"
        else:
            raise ValueError("Unknown trigger")

    async def _race_engineer(self, state: AgentState) -> AgentState:
        resp_example = {
            "assistant_coach": "Thanks - keep an eye on the tyre wear for now.",
            "driver": "Vedant - try to take care of the tyres going into turn 1. Seeing some locking..."
        }
        messages = [
            SystemMessage(
                "You are a sim-racing race engineer for Vedant - an iRacing enthusiast "
                "looking to improve on track. You will need to be able to talk to two people - "
                "Vedant directly (via driver radio) and the assistant coach working in the garage. "
                "The assistant coach's job is to help you in your role as engineer. They have access "
                "to recent session events as well as Vedant's telemetry and will routinely provide you with "
                "updates and analysis. Remember - you need to be able to maintain two independent "
                "conversations at the same time. When responding to the driver, remember to keep "
                "your answers short and useful. When responding to the assistant coach, provide a set "
                "of remarks. This could be what you want them to focus on, how you want them to change their "
                "analysis, or even just a simple thank you! "
                "Remember you too have access to a set of tools to perform quick checks yourself rather than "
                "relying on the assistant coach.\n"
                "Important Notes:\n"
                "- Do **NOT** hallucinate any findings. Make sure any data presented to the driver is directly supported "
                "by tools data or assistant coach messages.\n"
                "- Expect 'small talk' messages, e.g. 'thanks', 'will do' etc. from the driver. Here, **consider** "
                "replying casually to the driver with no comment to the assistant coach.\n"
                "Below is an example response (to the driver and/or coach). Note the JSON-structure; strictly keep to "
                "this format. Use `null` for any particular message if you don't want to send a message to that person.\n"
                f"{json.dumps(resp_example, indent=2)}"
            ),
            # TODO - truncate.
            # Includes labelled driver and assistant coach messages (both human messages)
            *state["race_engineer_messages"]
        ]

        resp = await self._model.ainvoke(
            messages,
            reasoning=True,
            # format="json"
        )

        state["race_engineer_messages"].append(
            resp
        )

        if self.contains_tool_calls(resp):
            return state

        try:
            resp_content = json.loads(resp.content)
        except JSONDecodeError as err:
            raise RuntimeError("Encountered bad message from race engineer.") from err

        if "assistant_coach" in resp_content and resp_content["assistant_coach"]:
            state["assistant_coach_messages"].append(
                HumanMessage(resp_content["assistant_coach"])
            )
        if "driver" in resp_content  and resp_content["driver"]:
            state["send_driver_message"] = True
            state["driver_messages"].append(
                AIMessage(resp_content["driver"])
            )
        return state

    async def _assistant_coach(self, state: AgentState) -> AgentState:
        example_events = [
            [
                EventStamp(
                    priority=EventPriority.HIGH,
                    lap_dist=0.2,
                    session_tick=100,
                    event=Event.POSSIBLE_LOCK_UP,
                    associated_data={
                        "Brake": 0.95
                    }
                ),
                EventStamp(
                    priority=EventPriority.HIGH,
                    lap_dist=0.5,
                    session_tick=200,
                    event=Event.POSSIBLE_LOCK_UP,
                    associated_data={
                        "Brake": 0.935
                    }
                )
            ]
        ]
        response_example = {
            "DistinctEventsAnalysed": ["PotentialLockUp"],
            "PatternsIdentified": "The driver appears to be locking up frequently when aggressive on the brakes (brake "
                                  "threshold exceeding 90%).",
            "Urgency": "MEDIUM",
            "Confidence": 0.8,
            "SuggestedActions": [
                "Advise driver to lower peak braking or move brake bias rearwards."
            ]
        }
        messages = [
            SystemMessage(
                "You are a sim-racing assistant coach. You will be provided with event data "
                "from a live iRacing session and it your job to perform an analysis and put "
                "forward a report (set of structured remarks) to the race engineer, who will then "
                "ultimately determine what to relay to the driver. The user in this case "
                "is the race engineer who, in their messages, may offer some remarks on your "
                "analysis, which you should consider in future analyses.\n"
                "An example is included below. Remember to strictly stick to the response format.\n"
                f"Events:\n{json.dumps(example_events)}\n\n"
                f"Example Response:\n{json.dumps(response_example)}"
            ),
            # TODO - truncate
            *state["assistant_coach_messages"]
        ]

        resp = await self._model.ainvoke(
            messages,
            reasoning=True,
            format="json"
        )

        try:
            _ = json.loads(resp.content)
        except JSONDecodeError as err:
            raise RuntimeError("Got invalid message from assistant coach.") from err

        state["race_engineer_messages"].append(
            AssistantCoachMessage(resp.content)
        )
        return state


    @staticmethod
    def contains_tool_calls(message: AnyMessage) -> bool:
        return hasattr(message, "tool_calls") and len(message.tool_calls) > 0


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
