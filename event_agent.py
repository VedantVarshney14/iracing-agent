import asyncio
import logging
import os
import queue
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

import iagent
from iagent import utils
from iagent.agent import DriverMessage, AgentState, get_initial_state, Agent
from iagent.audio.tts import TTS
from iagent.events.event_listener import EventListener
from iagent.events.events import EventStamp, EventPriority
from iagent.mcp_servers import garage_mcp, iracing_mcp
from iagent.priority_queue import ThreadPriorityQueue

logger = logging.getLogger(__name__)


class TriggerType(str, Enum):
    DRIVER_MESSAGE = "driver_message"
    HIGH_PRIORITY_EVENT = "high_priority_event"
    LOW_PRIORITY_EVENT_THRESHOLD = "low_priority_event_threshold"


@dataclass
class Trigger:
    trigger_type: TriggerType
    data: Any


def driver_input_thread(
        stop_event: threading.Event,
        loop: asyncio.AbstractEventLoop,
        enqueue_callback: Callable[[int, Trigger], None],
        default_msg: str = "Hey - how was my last lap?",
):
    """
    Blocking driver input running in a thread; pushes Trigger objects into the provided
    enqueue_callback with appropriate priority.
    """
    try:
        while not stop_event.is_set():
            try:
                raw = input("Driver message ([Enter] to use the default): ")
            except EOFError:
                break
            msg = raw or default_msg
            logger.info(f"Driver Message received: {msg}")
            # Driver messages are high-priority (lower numeric priority)
            loop.call_soon_threadsafe(
                enqueue_callback, 0, Trigger(TriggerType.DRIVER_MESSAGE, msg)
            )
    except Exception:
        logger.exception("Exception in driver input thread")
    finally:
        return


async def invoke_graph(state: AgentState, agent: Agent, tts: TTS) -> AgentState:
    """Run agent.graph.ainvoke and handle send_driver_message consistently."""
    state = await agent.graph.ainvoke(state)
    if state.get("send_driver_message"):
        if state.get("driver_messages"):
            ai_msg = state["driver_messages"][-1].content
            logger.info(f"AI Message: {ai_msg}")
            try:
                tts.generate(ai_msg)
            except Exception:
                logger.exception("TTS generation failed")
        state["send_driver_message"] = False
    return state


async def process_trigger(
        trigger: Trigger, state: AgentState, agent: Agent, tts: TTS
) -> AgentState:
    """Process a trigger by modifying state and running graph"""
    if trigger.trigger_type == TriggerType.DRIVER_MESSAGE:
        msg: str = trigger.data
        state["trigger"] = "driver"
        state["driver_messages"].append(HumanMessage(msg))
        state["race_engineer_messages"].append(DriverMessage(msg))
    else:
        event_stamp: EventStamp = trigger.data
        logger.info(f"Processing event: {event_stamp}")
        state["trigger"] = "events"
        state["current_events"].append(event_stamp)

    return await invoke_graph(state, agent, tts)


async def queue_get_async(pqueue: ThreadPriorityQueue) -> Any:
    """Await for the next item in the thread priority queue using run_in_executor.

    This avoids blocking the asyncio event loop while waiting for cross-thread items.
    """
    loop = asyncio.get_running_loop()

    def _blocking_get():
        # This will raise queue.Empty if empty; caller should handle and retry
        return pqueue.get_nowait()

    while True:
        try:
            item = await loop.run_in_executor(None, _blocking_get)
            return item
        except queue.Empty:
            # Sleep a tiny bit to avoid busy polling; yield control back to loop
            await asyncio.sleep(0.02)


async def main():
    logger.info("Setting up TTS")
    # TODO - load as config
    tts = TTS(phonetics={"Vedant": "/ˈvɪdænt/"})

    logger.info("Setting up Event Listener and unified priority queue")
    stop_event = threading.Event()
    prioritized_queue = ThreadPriorityQueue()

    # enqueue_callback used by EventListener to publish events into our prioritized queue
    def _enqueue_event(event: EventStamp):
        if event.priority == EventPriority.HIGH:
            trigger_type = TriggerType.HIGH_PRIORITY_EVENT
        elif event.priority == EventPriority.LOW:
            trigger_type = TriggerType.HIGH_PRIORITY_EVENT
        else:
            raise ValueError(f"Unknown priority event {event.priority}")
        prioritized_queue.put(event.priority, Trigger(trigger_type, event))

    event_listener = EventListener(enqueue_callback=_enqueue_event)

    # Run EventListener in its own thread with its own event loop so it doesn't get starved
    def _run_event_listener():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(event_listener.listen(stop_event))
        finally:
            loop.close()

    event_thread = threading.Thread(target=_run_event_listener, daemon=True)
    event_thread.start()

    # Start driver input thread which pushes DRIVER_MESSAGE triggers with highest priority (0)
    loop = asyncio.get_running_loop()
    driver_thread = threading.Thread(
        target=driver_input_thread,
        args=(stop_event, loop, lambda p, t: prioritized_queue.put(p, t)),
        daemon=True,
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
                "args": [garage_mcp.__file__, os.environ.get("GARAGE61_PAT", "")],
                "transport": "stdio",
            },
        }
    )

    async with (
        client.session("iracing") as iracing_session,
        client.session("garage") as garage_session,
    ):
        tools = await load_mcp_tools(iracing_session)
        tools += await load_mcp_tools(garage_session)
        agent = Agent(tools)

        state = get_initial_state()
        driver_thread.start()

        try:
            while True:
                # If there's a prioritized item available, process it first
                if not prioritized_queue.empty():
                    trigger = await queue_get_async(prioritized_queue)
                    state = await process_trigger(trigger, state, agent, tts)

                # No triggers available - keep polling after sleep
                await asyncio.sleep(0.05)

        finally:
            stop_event.set()
            driver_thread.join(timeout=2.0)
            event_thread.join(timeout=2.0)


if __name__ == "__main__":
    # Log preferences for runner and source package
    for _logger in (__name__, iagent.__name__):
        utils.setup_logger(_logger)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    resp = loop.run_until_complete(main())
