# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Bliss REPL (Read Eval Print Loop)"""


from __future__ import annotations
import html
import logging
from collections import deque

from prompt_toolkit import print_formatted_text, HTML
from prompt_toolkit.application import AppSession

from bliss.config import static
from bliss.common.logtools import elogbook
from bliss.common.protocols import ErrorReportInterface
from bliss.shell.cli.formatted_traceback import BlissTraceback, pprint_traceback


logger = logging.getLogger(__name__)


class ErrorReport(ErrorReportInterface):
    """
    Manage the behavior of the error reporting in the shell.

    - ErrorReport.expert_mode = False (default) => prints a user friendly error message without traceback
    - ErrorReport.expert_mode = True            => prints the full error message with traceback

    - ErrorReport.last_error stores the last error traceback

    """

    def __init__(self, style, app_session: AppSession):
        self._expert_mode = False
        self._history: deque[BlissTraceback] = deque(maxlen=100)
        self._nb_discarded = 0
        self._current_style = style
        self._is_loading_config = False
        self._app_session = app_session

    @property
    def expert_mode(self):
        return self._expert_mode

    @expert_mode.setter
    def expert_mode(self, enable):
        self._expert_mode = bool(enable)

    def append(self, error):
        if len(self._history) == self._history.maxlen:
            self._nb_discarded += 1
        self._history.append(error)

    def __len__(self):
        return len(self._history) + self._nb_discarded

    def __getitem__(self, index):
        if index < 0:
            index = len(self) + index

        if index < 0 or index >= len(self):
            raise IndexError("Index out of range")
        deque_index = index - self._nb_discarded
        if deque_index < 0 or deque_index >= len(self._history):
            raise IndexError(
                f"Exception[{index}] has been discarded, only the last {self._history.maxlen} exceptions are kept in history."
            )

        return self._history[deque_index]

    @property
    def is_loading_config(self):
        return self._is_loading_config

    @is_loading_config.setter
    def is_loading_config(self, loading):
        self._is_loading_config = bool(loading)

    def display_exception(self, exc_type, exc_value, tb, _with_elogbook=True):
        exc_logger = logging.getLogger("exceptions")
        app_session = self._app_session

        # BlissTraceback captures traceback information without holding any reference on its content
        fmt_tb = BlissTraceback(exc_type, exc_value, tb)

        # store BlissTraceback for later formatting
        self._history.append(fmt_tb)

        # publish full error to logger
        exc_logger.error(
            fmt_tb.format(
                disable_blacklist=False,
                max_nb_locals=15,
                max_local_len=200,
                show_locals=True,
            )
        )

        # Adapt the error message depending on the expert_mode
        if self.expert_mode:
            fmt_tb = self[-1].format(
                disable_blacklist=False,
                max_nb_locals=15,
                max_local_len=200,
                show_locals=True,
            )
            pprint_traceback(fmt_tb, self._current_style)
        else:
            if self.is_loading_config or isinstance(
                exc_value, static.ObjectCreationFailed
            ):
                e = exc_value
                causes = [e]
                while isinstance(e, static.ObjectCreationFailed):
                    e = e.__cause__
                    causes.append(e)

                if self.is_loading_config:
                    error_count_msg = f"[{len(self)-1}] "
                else:
                    error_count_msg = ""

                fmt_error = ""
                for i, e in enumerate(causes):
                    if i == 0:
                        fmt_error += error_count_msg
                    else:
                        fmt_error += (
                            f"{' ' * len(error_count_msg)}  {'    ' * (i - 1)}└─"
                        )
                    if isinstance(e, static.ObjectCreationFailed):
                        name = html.escape(e.name)
                        filename = html.escape(e.filename)
                        if i == 0:
                            fmt_error += f"Initialization of '<bold>{name}</bold>' <red>FAILED</red>  (see '<bold>{filename}</bold>')\n"
                        else:
                            fmt_error += f"Depending on initialization of '<bold>{name}</bold>'  (see '<bold>{filename}</bold>')\n"
                    else:
                        class_name = html.escape(e.__class__.__name__)
                        error = html.escape(str(e))
                        fmt_error += f"<red>{class_name}</red>: {error}\n"
                print_formatted_text(HTML(fmt_error), end="", output=app_session.output)

                if not self.is_loading_config:
                    print(f"( for more details type cmd 'last_error({len(self)-1})' )")
            else:
                print(
                    f"!!! === {exc_type.__name__}: {exc_value} === !!! ( for more details type cmd 'last_error({len(self)-1})' )",
                )

        if _with_elogbook:
            try:
                elogbook.error(f"{exc_type.__name__}: {exc_value}")
            except Exception:
                self.display_exception(exc_type, exc_value, tb, _with_elogbook=False)
