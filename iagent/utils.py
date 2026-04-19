import logging
import os
from pathlib import Path
from typing import Callable


class LoggingFormatter(logging.Formatter):
    FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
    DATEFMT = "%H:%M:%S"

    def __init__(self):
        super().__init__(fmt=self.FORMAT, datefmt=self.DATEFMT)


def setup_logger(name, level: int = logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(LoggingFormatter())
    logger.addHandler(handler)
    return logger


def get_data_path() -> Path:
    """Get path to reference data directory."""
    return (Path(__file__).parent / "data").absolute()


def get_debug_data_path() -> Path:
    """Get path to debug data directory."""
    fpath = (Path(__file__).parents[1] / "data").absolute()
    if not fpath.exists():
        raise ValueError(f"Debug data path {fpath} does not exist. Please create it and add any necessary files for debugging.")
    return fpath


def wrap_docs(wrapping_fun: Callable):
    """Gives the wrapping callable the same docstring as a specified callable. Useful for building LLM tools."""

    def decorator(fun: Callable):
        fun.__doc__ = wrapping_fun.__doc__
        return fun

    return decorator


def in_debug() -> bool:
    return os.environ.get("DEBUG", "false").upper() in {"1", "TRUE", "YES", "Y", "ON"}
