import logging
from pathlib import Path


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

# TODO - should remove this....
def get_misc_data_path() -> Path:
    """Get path to miscellaneous data directory."""
    return (Path(__file__).parents[1] / "misc-data").absolute()