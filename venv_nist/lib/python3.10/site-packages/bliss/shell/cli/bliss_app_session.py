# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Bliss App Session"""

from __future__ import annotations
from collections.abc import Generator

import functools
import typing
import builtins
import sys
import gevent
from contextlib import contextmanager

from prompt_toolkit.application import current
from prompt_toolkit.application import get_app_session
from bliss.shell.pt.default_style import get_style

from bliss.shell.pt.gevent_stdout_proxy import GeventStdoutProxy
from bliss.shell.cli.bliss_output import BlissOutput
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit import print_formatted_text
from prompt_toolkit.application import AppSession

if typing.TYPE_CHECKING:
    from prompt_toolkit.output import Output
    from prompt_toolkit.input import Input


builtin_print = builtins.print


class BlissAppSession(AppSession):
    def __init__(self, input: Input | None = None, output: Output | None = None):
        AppSession.__init__(self, input, output)
        self._stdout_proxy: GeventStdoutProxy

    def finalize_initialization(self):
        """This function have to be called after the registration of this object
        if the contextvar.
        """
        self._stdout_proxy = GeventStdoutProxy()

    def close(self):
        self._stdout_proxy.close()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(app={self.app!r})"

    @property
    def stdout_proxy(self) -> GeventStdoutProxy:
        """Returns a stdout proxy that can be used to print content over
        active application"""
        return self._stdout_proxy


def session_stream() -> Output:
    """Return the prompt-toolkit output used by the current executed greenlet/coroutine"""
    current_greenlet = gevent.getcurrent()
    app_session = None
    if isinstance(current_greenlet, gevent.Greenlet):
        app_session = current_greenlet.spawn_tree_locals.get("app_session")
    if app_session is None:
        app_session = get_app_session()
    return app_session.output


@functools.wraps(print)
def session_print(
    *objects, sep: str = " ", end: str = "\n", file=None, flush: bool = False
):
    """Depending on the current session, print on the right output"""
    if file is not None:
        builtin_print(*objects, sep=sep, end=end, file=file, flush=flush)
        return
    # Store app session in asyncio context
    # This is important to be properly used by run_in_terminal
    app_session = current.get_app_session()
    current._current_app_session.set(app_session)

    only_str = all([isinstance(o, str) for o in objects])
    empty = len(objects) == 0
    if only_str or empty:
        # Fallback here in case it contains '\r'
        # `print_formatted_text` remove any '\r'
        if current.get_app_or_none() is None:
            # There is no application so we dont have to wrap the print
            # This make it consistent with print_html and print_ansi
            stdout = app_session.output
        else:
            if isinstance(app_session, BlissAppSession):
                stdout = app_session.stdout_proxy
            else:
                builtin_print("!!! the following print was not protected")
                stdout = None
        builtin_print(*objects, sep=sep, end=end, file=stdout, flush=True)
        return

    # Normalize the objects as str, else print_formatted_text can try to
    # call callable objects like class types, infer formatted text from lists
    lobjects = [o if hasattr(o, "__pt_formatted_text__") else str(o) for o in objects]

    output = session_stream()
    print_formatted_text(
        *lobjects,
        sep=sep,
        end=end,
        output=output,
        flush=flush,
        style=get_style(),
    )


@contextmanager
def create_bliss_app_session(
    input: Input | None = None, output: Output | None = None
) -> Generator[BlissAppSession, None, None]:
    """
    Create a separate AppSession.

    This is useful if there can be multiple individual `AppSession`s going on.
    Like in the case of an Telnet/SSH server.
    """
    # If no input/output is specified, fall back to the current input/output,
    # whatever that is.
    if input is None:
        input = get_app_session().input
    if output is None:
        output = get_app_session().output

    # Create new `AppSession` and activate.
    session = BlissAppSession(input=input, output=output)

    token = current._current_app_session.set(session)
    session.finalize_initialization()
    try:
        yield session
    finally:
        session.close()
        current._current_app_session.reset(token)


@contextmanager
def bliss_app_session(
    input: Input | None = None,
    output: Output | None = None,
):
    """
    Create a separate AppSession for BLISS.

    This functionality uses contextvars.

    Arguments:
        input: Input for the application
        output: Output which identify the screen of the application. This output
                will be wrapped inside a dedicated BlissOutput.
    """
    if output is None:
        # This supports Vt100, win32, and no tty stdout
        output = create_output(stdout=sys.stdout)
    if not isinstance(output, BlissOutput):
        output = BlissOutput(output)
    try:
        with create_bliss_app_session(input, output) as app_session:
            builtins.print = session_print  # type: ignore
            yield app_session
    finally:
        builtins.print = builtin_print
