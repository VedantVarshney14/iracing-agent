import functools
import json
import logging
from typing import Any, Callable, Optional

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
    def get_current_telemetry_data(self, key: str) -> Optional[Any]:
        """
        Get current iRacing telemetry data.

        Parameters
        ----------
        key : str
            Telemetry item key.

        Examples
        --------
        >>> get_current_telemetry_data("Speed")
        >>> get_current_telemetry_data("AirDensity")

        Returns
        -------
        Optional[Any]
            Current telemetry data for specified key. If not available, value
            will be None.
        """

        if not self.is_alive:
            return None

        try:
            return self._ir[key]
        except (ValueError, RuntimeError):
            logger.exception(f"Error getting telemetry data. Requested item '{key}'.")
            return None

    @staticmethod
    @StatefulTools.register_tool
    def get_telemetry_definitions() -> dict[str, str]:
        """
        Get the definition of all telemetry items/keys.

        Returns
        -------
        dict[str, str]
        """
        with open(utils.get_data_path() / "vars.json", "rb") as f:
            return json.load(f)
