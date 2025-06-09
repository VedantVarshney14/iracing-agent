import logging

from iagent import utils
from iagent.mcp.iracing_mcp import mcp as iracing_mcp
from iagent.mcp.garage_mcp import mcp as garage_mcp

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    utils.setup_logger(logger.name)
    iracing_mcp.mount("garage", garage_mcp)
    logger.info("Starting MCP servers.")
    iracing_mcp.run(transport="stdio")
