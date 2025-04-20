import sys
import logging
from typing import Optional


def basic_config(
    logger: Optional[logging.Logger] = None,
    level: Optional[int] = None,
    format: Optional[str] = None,
) -> None:
    """
    :param logger: root logger when not provided
    :param level: logger log level
    :param str format:
    """
    if logger is None:
        logger = logging.getLogger()
    if level is not None:
        logger.setLevel(level)

    threshold_level = logging.WARNING

    class StdOutFilter(logging.Filter):
        def filter(self, record):
            return record.levelno < threshold_level

    class StdErrFilter(logging.Filter):
        def filter(self, record):
            return record.levelno >= threshold_level

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.addFilter(StdOutFilter())
    stdout_handler.setLevel(logging.DEBUG)
    logger.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.addFilter(StdErrFilter())
    stderr_handler.setLevel(threshold_level)
    logger.addHandler(stderr_handler)

    if format:
        formatter = logging.Formatter(format)
        stdout_handler.setFormatter(formatter)
        stderr_handler.setFormatter(formatter)
