# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

from typing import Any
import gevent
from prompt_toolkit.application import get_app_or_none
from prompt_toolkit.application import current

from bliss import _get_current_session
from .task import Task
from .proxy_task import ProxyTask


class TerminalNotAvailable(RuntimeError):
    """The session was not setup with a terminal"""

    def __init__(self):
        RuntimeError.__init__(self, "No terminal avaiable")


class TerminalAlreadyInUse(RuntimeError):
    """The terminal is already used by another task"""

    def __init__(self):
        RuntimeError.__init__(self, "Terminal already in use")


def text_in_box(lines: list[str]) -> str:
    width = max([len(line) for line in lines])
    for i, line in enumerate(lines):
        align = width - len(line)
        lines[i] = f"│ {line}{' ' * align} │"
    lines.insert(0, f"┌{'─' * (width + 2)}┐")
    lines.append(f"└{'─' * (width + 2)}┘")
    return "\n".join(lines)


def print_task_call(task: Task, args: tuple[Any, ...], kwargs: dict[str, Any]):
    result = [
        "EXECUTE A FUNCTION TRIGGERED REMOTELY",
        f"func:     {task.description}",
    ]
    for i, arg in enumerate(args):
        name = f"arg{i}:"
        result.append(f"{name:<10}{arg}")
    for kwname, kwarg in kwargs.items():
        name = f"{kwname}:"
        result.append(f"{name:<10}{kwarg}")
    print(text_in_box(result))


class RunInPromptTask(ProxyTask):
    """
    Inject input in the prompt to execute the requested task.

    As result the task is executed the same way a user would run it.
    All the features like text block are used, and the user can
    abort the task with CTRL-C.

    If there is no repl or the repl is busy, this raises an
    exception.
    """

    def __init__(self, task: Task):
        ProxyTask.__init__(self, task)
        bliss_session = _get_current_session()
        bliss_repl = bliss_session.bliss_repl
        if bliss_repl is None:
            raise TerminalNotAvailable()

        self._app_session = bliss_repl._app_session
        assert self._app_session is not None
        current._current_app_session.set(self._app_session)

        app = get_app_or_none()
        if app is None:
            # The prompt is an app. It is removed when something is executed
            # To allow other app to be connected
            raise TerminalAlreadyInUse()

        if app is not bliss_repl.app:
            # If the actual app is not the BLISS prompt,
            # we are about to do something else
            raise TerminalAlreadyInUse()

        if not bliss_repl.app.is_running:
            raise TerminalAlreadyInUse()

    def __call__(self, *args, **kwargs):
        result: Any = None
        raised: BaseException | None = None

        current._current_app_session.set(self._app_session)
        bliss_session = _get_current_session()
        bliss_repl = bliss_session.bliss_repl

        print_task_call(self._task, args, kwargs)

        event = gevent.event.Event()

        def call_real_func():
            nonlocal result, raised, event
            try:
                result = self._task(*args, **kwargs)
            except BaseException as e:
                raised = e
            finally:
                event.set()

        bliss_repl.get_globals()["run_call_from_remote"] = call_real_func
        input_text = bliss_repl.default_buffer.text
        try:
            bliss_repl.default_buffer.reset()
            bliss_repl.default_buffer.insert_text("run_call_from_remote()")
            bliss_repl.default_buffer.validate_and_handle()
            while True:
                try:
                    event.wait()
                except gevent.GreenletExit:
                    # Interruption from the REST API
                    bliss_repl.kill_current_eval()
                else:
                    break
        finally:
            del bliss_repl.get_globals()["run_call_from_remote"]
            # Restore the previous input
            for _ in range(10):
                if bliss_repl.app.is_running:
                    break
                gevent.sleep(0.1)
            bliss_repl.default_buffer.insert_text(input_text)

        if raised:
            if isinstance(raised, KeyboardInterrupt):
                # This have to be captured in order to not bubble up into asyncio
                raise gevent.GreenletExit

            raise raised

        return result
