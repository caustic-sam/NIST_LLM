# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Bliss Output"""

from __future__ import annotations

import re
from collections import deque
from contextlib import contextmanager
from typing import Optional
from bliss.shell.formatters.string import removed_ansi_sequence

from bliss.common import event
from bliss.shell.pt.proxy_output import ProxyOutput


class BlissOutput(ProxyOutput):
    """This class is used to keep track of the output history.

    It is meant to be used as a mixin with a prompt toolkit output.

    FIXME: It looks to be also designed to be also used as `io.IOBase`
           (see `isatty`) but as we have also wrap `print`, we should drop
           that.
    """

    _MAXLEN = 20

    def __init__(self, output):
        ProxyOutput.__init__(self, output=output)
        self._capture = False
        self.in_text_block = False
        self._capture_result = False
        self._output_buffer = []
        self._result_buffer = []
        self._cell_counter = 0
        self._cell_output_history: deque[str] = deque(maxlen=self._MAXLEN)

    @property
    @contextmanager
    def capture_stdout(self):
        self._capture = True
        try:
            yield
        finally:
            self._capture = False

    @property
    @contextmanager
    def capture_result(self):
        self._capture_result = True
        try:
            yield
        finally:
            self._capture_result = False

    def initialize_cell(self):
        self._output_buffer.clear()

    def finalize_cell(self, insert_blank_line_after_output: bool = False):
        """Store the current buffered output as 1 cell output in the history."""
        if self._output_buffer:
            output = "".join(self._output_buffer)
            self._output_buffer.clear()
        else:
            output = None
        if self._result_buffer:
            result = "".join(self._result_buffer)
            result = re.sub(
                r"^(\s+Out\s\[\d+\]:\s+)", "", result, count=1, flags=re.MULTILINE
            )
            self._result_buffer.clear()
        else:
            result = None

        if output is None and result is None:
            res = None
        else:
            if output is None:
                output = ""
            if result is None:
                result = ""
            else:
                if insert_blank_line_after_output:
                    # An extra return line was added
                    if result.endswith("\n"):
                        result = result[0:-1]

            if output == "" or output.endswith("\n"):
                sep = ""
            else:
                sep = "\n"
            res = f"{output}{sep}{result}"
            res = res.replace("\r\n", "\n")

        self._cell_output_history.append(res)
        self._cell_counter += 1

    def __getitem__(self, item: int) -> Optional[str]:
        """Note that the ptpython cell index starts counting from 1

        item > 0 will be interpreted as the cell index
        item < 0 will be interpreted as the most recent cell output (-1 is the last output)
        item == 0 raise IndexError

        The output value of a cell without output is `None`.
        """
        if not isinstance(item, int):
            raise TypeError(item)
        if self._cell_counter == 0:
            raise IndexError("No output.")
        if item > 0:
            # convert cell index to queue index
            idx = item - self._cell_counter - 1
            if idx >= 0:
                raise IndexError(f"the last cell is OUT [{self._cell_counter}]")
        elif item == 0:
            idx_min = max(self._cell_counter - self._MAXLEN + 1, 1)
            raise IndexError(f"the first available cell is OUT [{idx_min}]")
        elif (item + self._cell_counter) < 0:
            idx_min = max(self._cell_counter - self._MAXLEN + 1, 1)
            raise IndexError(f"the first available cell is OUT [{idx_min}]")
        else:
            idx = item
        try:
            return self._cell_output_history[idx]
        except IndexError:
            idx_min = max(self._cell_counter - self._MAXLEN + 1, 1)
            raise IndexError(f"the first available cell is OUT [{idx_min}]") from None

    def write(self, data):
        r_count = data.count("\r")
        rn_count = data.count("\r\n")  # legit line return
        if "\x1b[1A" in data or r_count > rn_count:
            # carriage return: text is erased,
            # and written again, should not be
            # in log stdout output
            super().write_raw(data)
            super().flush()
        else:
            if "\x1b" in data:
                # fancy text
                super().write_raw(data)
                text = removed_ansi_sequence(data)
            else:
                # normal text
                super().write(data)
                text = data

            if self._capture and not self.in_text_block:
                event.send(self, "output", text)
                self._output_buffer.append(text)
            if self._capture_result:
                event.send(self, "output", text)
                self._result_buffer.append(text)

            if data.endswith("\n"):
                self.flush()

    def append_stdout(self, content: str):
        """Append some content to the captured stdout only if the capture mode
        is active.
        """
        if self._capture:
            self._output_buffer.append(content)
            event.send(self, "output", content)

    def flush(self):
        super().flush()
        try:
            event.send(self, "flush")
        except IOError:
            pass
