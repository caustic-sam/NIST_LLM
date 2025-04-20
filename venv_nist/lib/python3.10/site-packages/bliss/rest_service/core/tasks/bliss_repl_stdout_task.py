# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

from flask_socketio import SocketIO

from bliss.common.event import connect, disconnect
from bliss import _get_current_session
from .task import Task
from .proxy_task import ProxyTask


class BlissReplStdoutTask(ProxyTask):
    """Capture the stdout of the BLISS REPL."""

    def __init__(self, task: Task, socketio: SocketIO):
        ProxyTask.__init__(self, task)
        self._socketio: SocketIO = socketio

    def __call__(self, *args, **kwargs):
        bliss_session = _get_current_session()
        output = bliss_session.bliss_repl._app_session.output
        connect(output, "output", self._on_stdout)
        try:
            return self._task(*args, **kwargs)
        finally:
            disconnect(output, "output", self._on_stdout)

    def _on_stdout(self, message: str):
        self._socketio.emit(
            "stdout", {"call_id": self.task_id, "message": message}, namespace="/call"
        )
