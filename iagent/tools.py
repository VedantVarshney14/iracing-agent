import functools
import json
import logging
from typing import Any, Callable

import irsdk
from langchain_core import tools

from iagent import utils

logger = logging.getLogger(__name__)


class StatefulTools:
    """
    A utility class to create langchain tools that are stateful (i.e.
    are instance methods which reference `self`.
    """

    @staticmethod
    def _make_tool(func: Callable) -> tools.Tool:
        """Build a langchain tool from an instance method."""

        @tools.tool
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    def build_tools(self) -> dict[str, tools.Tool]:
        """Build all registered tools."""
        built_tools = {}
        for name in dir(self):
            attr = getattr(self, name)
            if callable(attr) and getattr(attr, "_is_registered_tool", False):
                built_tools[name] = self._make_tool(attr)

        return built_tools

    @staticmethod
    def register_tool(func: Callable):
        """Mark an instance method as a tool (to be built later)."""
        func._is_registered_tool = True
        return func


class IRacingTools(StatefulTools):

    def __init__(self, ir: irsdk.IRSDK):
        super().__init__()
        self._ir = ir

    @property
    def is_alive(self) -> bool:
        return self._ir.is_initialized and self._ir.is_connected

    @StatefulTools.register_tool
    def get_current_telemetry_data(self) -> dict[str, Any]:
        """
        Get current iRacing telemetry data.

        Notes
        -----
        If a telemetry item cannot be retrieved, the key will be included
        in the response but the value will be `None`.


        Returns
        -------
        dict[str, Any]
            Current telemetry data.
        """
        telemetry = {}

        if not self.is_alive:
            return telemetry

        for key in self._ir.var_headers_names:
            try:
                telemetry[key] = self._ir[key]
            except (ValueError, RuntimeError):
                logger.exception("Error getting telemetry data")
                telemetry[key] = None
        return telemetry

    @staticmethod
    @StatefulTools.register_tool
    def get_telemetry_definitions() -> dict[str, str]:
        """
        Get the definition of all telemetry items as JSON.

        Returns
        -------
        dict[str, str]
        """
        with open(utils.get_data_path() / "vars.json", "rb") as f:
            return json.load(f)
