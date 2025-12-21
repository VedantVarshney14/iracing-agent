import logging
import os
import sys

from iagent import utils
from iagent.mcp_servers.iracing_mcp import mcp as iracing_mcp
from iagent.mcp_servers.garage_mcp import mcp as garage_mcp

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    utils.setup_logger(logger.name)
    if len(sys.argv) > 1:
        os.environ["GARAGE61_PAT"] = sys.argv[1]
    iracing_mcp.mount("garage", garage_mcp)
    logger.info("Starting MCP servers.")
    iracing_mcp.run(transport="stdio")
