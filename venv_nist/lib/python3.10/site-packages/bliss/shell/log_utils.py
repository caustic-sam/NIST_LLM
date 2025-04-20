# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Bliss prompt log utils

This module is used for early logging from imports, when BLISS subsystems
are not yet initialized.

Try to reduce as much as possible to amount of imports used here.
"""

from __future__ import annotations
from collections.abc import Iterator

import contextlib
import logging


def early_logging_startup():
    """Called before any imports"""
    # Redirection the warnings into the logging system
    logging.captureWarnings(True)


class LogInfo:
    def __init__(self):
        self.count: int = 0


@contextlib.contextmanager
def filter_import_warnings(ignore_warnings=True) -> Iterator[LogInfo]:
    # Hide the warnings from the users
    global deprecation_warning_logger

    count = 0

    class IgnoreAndCountLogFilter(logging.Filter):
        def filter(self, record):
            nonlocal count
            count += 1
            return False

    countLogFilter = IgnoreAndCountLogFilter()

    info = LogInfo()

    if ignore_warnings:
        warnings_logger = logging.getLogger("py.warnings")
    else:
        warnings_logger = None

    if warnings_logger:
        warnings_logger.addFilter(countLogFilter)
    else:
        # The logging handlers are not yet initialized
        # disabling this will display the logs in stdout
        logging.captureWarnings(False)
    try:
        yield info
    finally:
        if warnings_logger:
            warnings_logger.removeFilter(countLogFilter)
        else:
            logging.captureWarnings(True)

    info.count = count


@contextlib.contextmanager
def filter_warnings():
    # Hide the warnings from the users
    warnings_logger = logging.getLogger("py.warnings")

    warnings_logger.disabled = True
    try:
        yield
    finally:
        warnings_logger.disabled = False


def setup_loggers_to_ignore():
    # disable those ugly loggers from jedi
    logging.getLogger("parso.python.diff").disabled = True
    logging.getLogger("parso.cache").disabled = True


def logging_startup(
    log_level="WARNING", fmt="%(levelname)s %(asctime)-15s %(name)s: %(message)s"
):
    """
    Provides basicConfig functionality to bliss activating at proper level the root loggers
    """

    setup_loggers_to_ignore()

    from bliss import global_log  # this is not to pollute the global namespace

    # save log messages format
    global_log.set_log_format(fmt)  # set the format of the handlers of the root logger
    global_log._LOG_DEFAULT_LEVEL = log_level  # to restore level of non-BlissLoggers

    # setting startup level for session and bliss logger
    logging.getLogger("global").setLevel(log_level)
    logging.getLogger("bliss").setLevel(log_level)
    logging.getLogger("flint").setLevel(log_level)

    global_log.start_stdout_handler()

    # Beacon logging handler through SocketServer
    from bliss.config.conductor.client import get_log_server_address

    try:
        host, port = get_log_server_address()
    except RuntimeError:
        pass
    else:
        global_log.start_beacon_handler((host, port))
