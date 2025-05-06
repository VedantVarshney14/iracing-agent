import logging

import irsdk
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

import iagent
from iagent import utils
from iagent.tools import IRacingTools

logger = logging.getLogger(__name__)


LLM_PROMPT = (
    "You are simracing coach named Steve. Your job is to coach "
    "Vedant improve at the racing simulator iRacing. You will "
    "have access to his live telemetry and session data. Reply to Vedant's "
    "questions and advise him so he can be the best racing "
    "driver possible! Remember to keep your answers short "
    "(as though you are speaking over the radio while he is "
    "in the car). /no_think"
)


def main():
    ir = irsdk.IRSDK()
    # TODO - remove data load
    ir.startup(
        test_file="data.bin"
    )
    itools = IRacingTools(ir)
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

    messages = agent.invoke(
        {
            "messages": [
                ("human", "What's my speed?")
            ]
        }
    )
    pass



if __name__ == '__main__':
    utils.setup_logger(iagent.__name__)
    utils.setup_logger(__name__)
    main()
