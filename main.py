import logging
from threading import Thread, Event

import irsdk
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

import iagent
from iagent import utils, db
from iagent.db.tables import Base
from iagent.telemetry import TelemetryCollectionClient
from iagent.tools import IRacingTools

logger = logging.getLogger(__name__)


LLM_PROMPT = (
    "You are sim-racing coach named Steve. Your job is to coach "
    "Vedant improve at the racing simulator iRacing. You will "
    "have access to his live telemetry and session data. Reply to Vedant's "
    "questions and advise him so he can be the best racing "
    "driver possible! Remember to keep your answers short "
    "(as though you are speaking over the radio while he is "
    "in the car). /no_think"
)


def main():
    # Note engine is threadsafe
    engine = db.get_engine()
    Base.metadata.create_all(engine, checkfirst=True)

    ir = irsdk.IRSDK()
    # TODO - remove data load
    ir.startup(
        test_file="data.bin"
    )

    tclient = TelemetryCollectionClient(
        engine=engine,
        ir=ir
    )


    logger.info("Setting up tools.")
    itools = IRacingTools(ir, engine)
    built_tools = list(itools.build_tools().values())

    model = ChatOllama(
        model="qwen3:14b",
        temperature=0
    )

    agent = create_react_agent(
        model,
        built_tools,
        prompt=LLM_PROMPT,
        debug=True
    )

    stop_telem_event = Event()
    telem_thread = Thread(
        target=tclient.collect,
        kwargs={
            "stop": stop_telem_event
        }
    )

    logger.info("Starting telemetry collection.")
    telem_thread.start()

    try:
        messages = agent.invoke(
            {
                "messages": [
                    ("human", "What's the air temperature right now?")
                ]
            }
        )
        pass
    except KeyboardInterrupt:
        stop_telem_event.set()
        telem_thread.join()



if __name__ == '__main__':
    utils.setup_logger(iagent.__name__)
    utils.setup_logger(__name__)
    main()
