import asyncio
import logging
import os
import queue
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import click
import yaml
from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

import sys

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
        response_ready: threading.Event,
        default_msg: str = "Hey - how was my last lap?",
):
    """
    Blocking driver input running in a thread; pushes Trigger objects into the provided
    enqueue_callback with appropriate priority.

    Blocks the next prompt until `response_ready` is set by the main loop, so the
    driver cannot submit a second message while the AI is still responding.
    """
    try:
        while not stop_event.is_set():
            try:
                raw = input("Driver message ([Enter] to use the default): ")
            except EOFError:
                break
            msg = raw or default_msg
            print(f"Driver Message received: {msg}")
            response_ready.clear()
            loop.call_soon_threadsafe(
                enqueue_callback, 0, Trigger(TriggerType.DRIVER_MESSAGE, msg)
            )
            response_ready.wait()
    except Exception:
        logger.exception("Exception in driver input thread")
    finally:
        return


async def invoke_graph(state: AgentState, agent: Agent, tts: Optional[TTS]) -> AgentState:
    """Run agent.graph.ainvoke and handle send_driver_message consistently."""
    state = await agent.graph.ainvoke(state)
    if state.get("send_driver_message"):
        if state.get("driver_messages"):
            ai_msg = state["driver_messages"][-1].content
            print(f"AI Message: {ai_msg}")
            try:
                if tts is not None:
                    tts.generate(ai_msg)
                else:
                    logger.debug("NO_TTS set - skipping TTS generation")
            except Exception:
                logger.exception("TTS generation failed")
        state["send_driver_message"] = False
    return state


async def process_trigger(
        trigger: Trigger, state: AgentState, agent: Agent, tts: Optional[TTS]
) -> AgentState:
    """Process a trigger by modifying state and running graph"""
    if trigger.trigger_type == TriggerType.DRIVER_MESSAGE:
        msg: str = trigger.data
        state["trigger"] = "driver"
        prev_driver_count = len(state["driver_messages"])
        state["driver_messages"].append(HumanMessage(msg))
        state["race_engineer_messages"].append(DriverMessage(msg))
        state = await invoke_graph(state, agent, tts)
        if len(state["driver_messages"]) == prev_driver_count + 1:
            logger.warning("Driver message processed but no response was sent to the driver.")
        return state
    else:
        event_stamp: EventStamp = trigger.data
        logger.info(f"Processing event: {event_stamp}")
        state["trigger"] = "events"
        state["current_events"].append(event_stamp)

    return await invoke_graph(state, agent, tts)


async def queue_get_async(pqueue: ThreadPriorityQueue) -> Any:
    """Await for the next item in the thread priority queue using run_in_executor."""
    loop = asyncio.get_running_loop()

    def _blocking_get():
        return pqueue.get_nowait()

    while True:
        try:
            item = await loop.run_in_executor(None, _blocking_get)
            return item
        except queue.Empty:
            await asyncio.sleep(0.02)


async def main(phonetics: Optional[dict[str, str]]):
    if os.environ.get("NO_TTS"):
        logger.info("Skipping TTS")
        tts = None
    else:
        logger.info("Setting up TTS")
        tts = TTS(phonetics=phonetics)

    logger.info("Setting up Event Listener and unified priority queue")
    stop_event = threading.Event()
    prioritized_queue = ThreadPriorityQueue()

    def _enqueue_event(event: EventStamp):
        if event.priority == EventPriority.HIGH:
            trigger_type = TriggerType.HIGH_PRIORITY_EVENT
        elif event.priority == EventPriority.LOW:
            trigger_type = TriggerType.LOW_PRIORITY_EVENT_THRESHOLD
        else:
            raise ValueError(f"Unknown priority event {event.priority}")
        prioritized_queue.put(event.priority, Trigger(trigger_type, event))

    event_listener = EventListener(enqueue_callback=_enqueue_event)

    def _run_event_listener():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(event_listener.listen(stop_event))
        finally:
            loop.close()

    event_thread = threading.Thread(target=_run_event_listener, daemon=True)
    event_thread.start()

    loop = asyncio.get_running_loop()
    response_ready = threading.Event()
    response_ready.set()
    driver_thread = threading.Thread(
        target=driver_input_thread,
        args=(stop_event, loop, lambda p, t: prioritized_queue.put(p, t), response_ready),
        daemon=True,
    )

    logger.info("Setting up tools")
    client = MultiServerMCPClient(
        {
            "iracing": {
                "url": f"http://localhost:{os.environ.get('MCP_PORT', 8000)}/mcp",
                "transport": "streamable_http",
            }
        }
    )

    async with (
        client.session("iracing") as session
    ):
        tools = await load_mcp_tools(session)
        agent = Agent(tools)

        state = get_initial_state()
        driver_thread.start()

        try:
            while True:
                if not prioritized_queue.empty():
                    trigger = await queue_get_async(prioritized_queue)
                    state = await process_trigger(trigger, state, agent, tts)
                    if trigger.trigger_type == TriggerType.DRIVER_MESSAGE:
                        response_ready.set()

                await asyncio.sleep(0.05)

        finally:
            stop_event.set()
            driver_thread.join(timeout=2.0)
            event_thread.join(timeout=2.0)


@click.command()
@click.option(
    "--env",
    "env_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a .env file to load before starting.",
)
@click.option(
    "--phonetics",
    "phonetics_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a YAML file mapping words to phonetic strings for TTS.",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug mode (sets DEBUG=1; uses recorded telemetry instead of live iRacing).",
)
def cli(env_file: Optional[Path], phonetics_file: Optional[Path], debug: bool):
    if env_file:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=True)
        logger.info(f"Loaded environment from {env_file}")

    if debug:
        os.environ["DEBUG"] = "1"

    phonetics: Optional[dict[str, str]] = None
    if phonetics_file:
        with open(phonetics_file) as f:
            phonetics = yaml.safe_load(f)

    for _logger in (iagent.__name__,):
        level = logging.DEBUG if debug else logging.INFO
        utils.setup_logger(_logger, level=level)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main(phonetics))


if __name__ == "__main__":
    cli()
