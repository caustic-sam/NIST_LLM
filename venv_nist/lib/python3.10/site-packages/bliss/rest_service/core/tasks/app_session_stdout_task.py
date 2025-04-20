# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import gevent
from flask_socketio import SocketIO
from prompt_toolkit.application import current
from prompt_toolkit.application import AppSession
from prompt_toolkit.output import Output
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.data_structures import Size

from bliss.common.event import connect, disconnect, send
from .task import Task
from .proxy_task import ProxyTask


class _EventOutput(Output):
    """Redirect `write` output as a louis event"""

    def __init__(self):
        pass

    def write(self, data: str) -> None:
        "Write text (Terminal escape sequences will be removed/escaped.)"
        data = data.replace("\r\n", "\n")
        send(self, "write", data)

    def write_raw(self, data: str) -> None:
        "Write text."
        # send(self, "write", data)

    def fileno(self) -> int:
        "There is no sensible default for fileno()."
        raise NotImplementedError

    def encoding(self) -> str:
        return "utf-8"

    def set_title(self, title: str) -> None:
        pass

    def clear_title(self) -> None:
        pass

    def flush(self) -> None:
        pass

    def erase_screen(self) -> None:
        pass

    def enter_alternate_screen(self) -> None:
        pass

    def quit_alternate_screen(self) -> None:
        pass

    def enable_mouse_support(self) -> None:
        pass

    def disable_mouse_support(self) -> None:
        pass

    def erase_end_of_line(self) -> None:
        pass

    def erase_down(self) -> None:
        pass

    def reset_attributes(self) -> None:
        pass

    def set_attributes(self, attrs, color_depth: ColorDepth) -> None:
        pass

    def disable_autowrap(self) -> None:
        pass

    def enable_autowrap(self) -> None:
        pass

    def cursor_goto(self, row: int = 0, column: int = 0) -> None:
        pass

    def cursor_up(self, amount: int) -> None:
        pass

    def cursor_down(self, amount: int) -> None:
        pass

    def cursor_forward(self, amount: int) -> None:
        pass

    def cursor_backward(self, amount: int) -> None:
        pass

    def hide_cursor(self) -> None:
        pass

    def show_cursor(self) -> None:
        pass

    def set_cursor_shape(self, cursor_shape) -> None:
        pass

    def reset_cursor_shape(self) -> None:
        pass

    def ask_for_cpr(self) -> None:
        pass

    def bell(self) -> None:
        pass

    def enable_bracketed_paste(self) -> None:
        pass

    def disable_bracketed_paste(self) -> None:
        pass

    def scroll_buffer_to_prompt(self) -> None:
        pass

    def get_size(self) -> Size:
        return Size(rows=40, columns=80)

    def get_rows_below_cursor_position(self) -> int:
        return 40

    def get_default_color_depth(self) -> ColorDepth:
        return ColorDepth.DEPTH_1_BIT


class AppSessionStdoutTask(ProxyTask):
    """
    Setup a dedicated app session to the coroutine in order to capture
    stdout occuring.
    """

    def __init__(self, task: Task, socketio: SocketIO):
        ProxyTask.__init__(self, task)
        self._socketio: SocketIO = socketio

    def __call__(self, *args, **kwargs):
        event_output = _EventOutput()
        app_session = AppSession(None, event_output)
        current._current_app_session.set(app_session)
        g = gevent.getcurrent()
        g.spawn_tree_locals["app_session"] = app_session

        connect(event_output, "write", self._on_stdout)
        try:
            result = self._task(*args, **kwargs)
        finally:
            disconnect(event_output, "write", self._on_stdout)

        return result

    def _on_stdout(self, message: str, *args, **kwargs):
        self._socketio.emit(
            "stdout", {"call_id": self.task_id, "message": message}, namespace="/call"
        )
