import logging


class LoggingFormatter(logging.Formatter):
    """Log lines as `[time] [level] message`."""

    FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
    DATEFMT = "%H:%M:%S"

    def __init__(self):
        super().__init__(fmt=self.FORMAT, datefmt=self.DATEFMT)


def setup_logger(name, level: int = logging.INFO):
    """The named logger at LEVEL, writing to stderr (so stdout stays clean for `--json`); safe to
    call more than once."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if (
        not logger.handlers
    ):  # idempotent: the CLI may be invoked repeatedly in one process
        handler = (
            logging.StreamHandler()
        )  # stderr, so stdout stays clean for --json output
        handler.setFormatter(LoggingFormatter())
        logger.addHandler(handler)
    return logger
