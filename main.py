import logging

import irsdk

import iagent
from iagent import utils
from iagent.tools import IRacingTools

logger = logging.getLogger(__name__)


def main():
    ir = irsdk.IRSDK()
    ir.startup()
    itools = IRacingTools(ir)
    built_tools = itools.build_tools()



if __name__ == '__main__':
    utils.setup_logger(iagent.__name__)
    utils.setup_logger(__name__)
    main()
