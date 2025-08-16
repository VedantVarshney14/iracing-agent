import asyncio
import logging
import os
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_ollama import ChatOllama
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

import iagent
from iagent import utils
from iagent.events.events import EventStamp, EventPriority, Event
from iagent.mcp import garage_mcp
from iagent.serialize import json

logger = logging.getLogger(__name__)


class AgentState(MessagesState):
    """State for the agentic system"""
    current_events: list[EventStamp]
    # agent_memory: dict[str, Any]
    # current_goal: Optional[str]
    # action_plan: list[str]
    reasoning: Optional[str]
    confidence: Optional[float]
    tools_used: list[str]


class Agent:
    def __init__(self, tools):
        self._model = ChatOllama(
            model="qwen3:4b",
            temperature=0
        ).bind_tools(tools)
        self.graph = self._build_graph(tools).compile()

    def _build_graph(self, tools) -> StateGraph:
        graph = StateGraph(AgentState)

        # Agent reasoning nodes
        graph.add_node("analyze_events", self._analyze_events)
        graph.add_node("plan_response", self._plan_response)
        graph.add_node("tools", ToolNode(tools))  # Use the ToolNode
        graph.add_node("reflect_and_learn", self._reflect_and_learn)
        graph.add_node("update_memory", self._update_memory)

        # Decision flow
        # graph.add_edge("analyze_events", "plan_response")
        graph.add_conditional_edges(
            "analyze_events",
            self._should_use_tools,
            {
                "use_tools": "tools",
                "skip_tools": "reflect_and_learn"
            }
        )
        graph.add_edge("tools", "reflect_and_learn")
        graph.add_edge("reflect_and_learn", "update_memory")
        graph.add_edge("update_memory", "__end__")

        graph.set_entry_point("analyze_events")
        return graph

    async def _analyze_events(self, state: AgentState) -> AgentState:
        """Agent analyzes the events using reasoning and pattern recognition"""
        response_format = {
            "DistinctEventsAnalysed": ["PotentialLockUp"],
            "PatternsIdentified": "A description of the patterns you've identified with the events data.",
            "Confidence": "Estimate for confidence in conclusions, e.g. 0.8",
            "Urgency": "One of LOW, MEDIUM, HIGH",
            "SuggestedActions": [
                "A list of suggested actions to take, including driver radio and collecting further data using "
                "available tools."
            ]
        }
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
                "Check tyre wear to gauge depth of damage caused by lock-ups.",
                "Communicate tips to driver."
            ]
        }
        response = await self._model.ainvoke(
            [
                SystemMessage(
                    "You are a sim-racing analysis agent. You will be provided with some "
                    "recent session events and it is your job to determine a response strategy. "
                    "Use the below as an example of how to format your response. You must strictly "
                    "conform to this format.\n"
                    f"{json.dumps(response_format)}"
                ),
                HumanMessage(
                    json.dumps(example_events)
                ),
                AIMessage(
                    json.dumps(response_example)
                ),
                HumanMessage(
                    json.dumps(state["current_events"][-100:]),
                )
            ],
            format="json",
            reasoning=True
        )
        return {
            **state,
            # Don't parse JSON - allow for small mistakes
            "reasoning": response.content
        }

    def _plan_response(self, state: AgentState) -> AgentState:
        """Agent creates an action plan based on analysis"""
        return state

    def _should_take_action(self, state: AgentState) -> str:
        """Agent decides whether to take action or wait for more information"""
        return state

    def _execute_actions(self, state: AgentState) -> AgentState:
        """Agent executes the planned actions"""
        return state

    def _reflect_and_learn(self, state: AgentState) -> AgentState:
        """Agent reflects on its actions and learns from the outcomes"""
        return state

    def _update_memory(self, state: AgentState) -> AgentState:
        """Agent updates its long-term memory"""
        return state

    def _should_use_tools(self, state: AgentState) -> str:
        """Agent decides whether to use tools based on the situation"""
        events = state["current_events"]
        messages = state.get("messages", [])

        # Use tools if we have tool calls planned, or if we have significant events
        has_tool_calls = any(msg.get("tool_calls") for msg in messages)
        has_significant_events = any(e.priority in [EventPriority.HIGH] for e in events)

        if has_tool_calls or has_significant_events:
            print("🔧 Agent deciding to use tools")
            return "use_tools"
        else:
            print("⏭️ Agent skipping tools for low priority events")
            return "skip_tools"


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
        agent = Agent(tools)

        # Initial state
        state = AgentState(
            current_events=[
                EventStamp(
                    priority=EventPriority.HIGH,
                    lap_dist=0.22,
                    session_tick=200,
                    event=Event.POSSIBLE_LOCK_UP
                ),
                EventStamp(
                    priority=EventPriority.LOW,
                    lap_dist=0.01,
                    session_tick=1000,
                    event=Event.POSSIBLE_LOCK_UP
                ),
                EventStamp(
                    priority=EventPriority.HIGH,
                    lap_dist=0.18,
                    session_tick=1200,
                    event=Event.POSSIBLE_LOCK_UP
                )
            ],
            messages=[],
            reasoning=None,
            confidence=None,
            tools_used=None
        )

        # TODO - loop
        resp = await agent.graph.ainvoke(state)


if __name__ == '__main__':
    utils.setup_logger(iagent.__name__)
    utils.setup_logger(__name__)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    resp = loop.run_until_complete(main())
