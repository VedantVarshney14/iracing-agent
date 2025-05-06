import functools
import logging
from typing import Any, Callable

import irsdk
from langchain_core import tools

logger = logging.getLogger(__name__)


class StatefulTools:

    @staticmethod
    def _make_tool(func: Callable) -> tools.Tool:
        @tools.tool
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    def build_tools(self) -> dict[str, tools.Tool]:
        built_tools = {}
        for name in dir(self):
            attr = getattr(self, name)
            if callable(attr) and getattr(attr, "_is_registered_tool", False):
                built_tools[name] = self._make_tool(attr)

        return built_tools

    @staticmethod
    def register_tool(func: Callable):
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

    def get_tools(self):
        return [self.get_current_telemetry_data]
