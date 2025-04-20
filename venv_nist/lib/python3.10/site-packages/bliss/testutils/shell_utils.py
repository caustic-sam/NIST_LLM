# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations
import asyncio
import io
import os
import pytest
import sys
import typing
from collections.abc import Callable, Generator
from contextlib import contextmanager
import gevent

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.output import Output
from prompt_toolkit.application import current

from bliss.shell.cli.repl import cli
from bliss.shell.cli.bliss_repl import BlissRepl
from bliss.shell.cli.bliss_app_session import bliss_app_session
from bliss.common.greenlet_utils import asyncio_gevent


@pytest.fixture
def clear_pt_context():
    """Clear the context used by prompt-toolkit in order to isolate tests"""
    from prompt_toolkit.application import current

    app_session = current._current_app_session.get()
    is_default = app_session._input is None and app_session._output is None
    yield
    if is_default:
        app_session._input = None
        app_session._output = None


@pytest.fixture
def as_bliss_shell():
    from bliss.shell.pt import utils
    from bliss.shell.pt.text_block_app import TextBlockApplication

    def always_true():
        return True

    old = utils.can_use_text_block
    utils.can_use_text_block = always_true
    TextBlockApplication._CHECK_GREENLET = False
    yield
    utils.can_use_text_block = old
    TextBlockApplication._CHECK_GREENLET = True


@pytest.fixture
def no_text_block():
    from bliss.shell.pt import utils
    from bliss.shell.pt.text_block_app import TextBlockApplication

    def always_false():
        return False

    old = utils.can_use_text_block
    utils.can_use_text_block = always_false
    TextBlockApplication._CHECK_GREENLET = False
    yield
    utils.can_use_text_block = old
    TextBlockApplication._CHECK_GREENLET = True


@pytest.fixture
def asyncio_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture
def pt_test_context(asyncio_event_loop):
    class SimulatedOutput(PlainTextOutput):
        def __init__(self):
            self._cursor_up: bool = False
            self._output_memory = io.StringIO()
            PlainTextOutput.__init__(self, self._output_memory)

        def displayed_text(self) -> str:
            return self._output_memory.getvalue()

        def write(self, data: str):
            self._cursor_up = False
            super().write(data)

        def write_raw(self, data: str):
            self._cursor_up = False
            super().write_raw(data)

        def cursor_up(self, amount: int) -> None:
            # Just a guess for now
            self._cursor_up = True
            super().cursor_up(amount)

        def erase_down(self):
            if self._cursor_up:
                # Just a guess for now
                self._clear()
            super().erase_down()

        def _clear(self):
            """Clear the actual content of the buffer"""
            self._output_memory.seek(0, os.SEEK_SET)
            self._output_memory.truncate(0)

        def _flush_app(self):
            """Called internally by BLISS when an application is done"""
            print(self.displayed_text())
            self._clear()

    actions = []

    class Context:
        def __init__(self, pipe_input):
            self.input = pipe_input
            self.output = SimulatedOutput()
            self._app_session = None

        @property
        def app_session(self):
            return self._app_session

        def send_input(self, chars: str):
            self.input.send_text(chars)

        def send_input_later(
            self,
            timeout: float,
            chars: str,
            wait_callable: Callable[[], None] | None = None,
        ):
            def do_later():
                gevent.sleep(timeout)
                if wait_callable is not None:
                    wait_callable()
                self.input.send_text(chars)

            g = gevent.spawn(do_later)
            actions.append(g)

    with create_pipe_input() as pipe_input:
        context = Context(pipe_input)
        with create_app_session(
            input=context.input, output=context.output
        ) as app_session:
            context._app_session = app_session
            yield context

    for g in actions:
        gevent.kill(g)


class IntrospectBlissRepl(BlissRepl):
    """BlissRepl which can be introspected for unittest"""

    def send_input(self, text: str):
        """Send and run an input in this repl"""
        if not text.endswith("\r"):
            # Make sure the text will be executed
            text += "\r"
        # Execute single loop in the repl
        self.app.input.send_text(text)
        expression = self.app.run()
        self.run_and_show_expression(expression)

    @contextmanager
    def run_context(self):
        """Run the prompt until the end of the context"""

        async def run_async():
            current._current_app_session.set(self._app_session)
            await self.run_async()

        g = asyncio_gevent.future_to_greenlet(run_async())
        # FIXME: This have to wait until the app became the active one
        gevent.sleep(1.0)
        try:
            yield
        finally:
            self.app.input.send_text(chr(0x4))  # ctrl-d
            g.join()


@contextmanager
def start_bliss_repl(
    locals_dict=None,
    confirm_exit=False,
    vt100: bool = False,
    no_cli: bool = False,
    stdout: typing.TextIO | None = None,
    **kwargs,
) -> Generator[IntrospectBlissRepl, None, None]:
    """
    Arguments:
        vt100: If true, Use a vt100 output
        no_cli: If true, make sure the `cli` function is not used
    """
    if locals_dict is None:
        locals_dict = {}
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.output.color_depth import ColorDepth

    if stdout is None:
        stdout = sys.stdout

    with create_pipe_input() as input:
        output: Output
        if vt100:

            def get_size() -> Size:
                # Make sure the terminal size does not change between tests
                return Size(rows=200, columns=80)

            output = Vt100_Output(
                stdout,
                get_size,
                term="dump",
                default_color_depth=ColorDepth.DEPTH_8_BIT,
            )
        else:
            output = PlainTextOutput(stdout)
        with bliss_app_session(input=input, output=output) as app_session:
            if "session_name" in kwargs and not no_cli:
                # full initialization of a session
                br = cli(
                    repl_class=IntrospectBlissRepl,
                    locals=locals_dict,
                    app_session=app_session,
                    **kwargs,
                )
            else:
                br = IntrospectBlissRepl(
                    style="default",
                    theme_mode="dark",
                    get_globals=lambda: locals_dict,
                    app_session=app_session,
                    **kwargs,
                )
            br.confirm_exit = confirm_exit
            try:
                yield br
            finally:
                br.exit()
                if br.bliss_session is not None:
                    br.bliss_session.close()


@pytest.fixture
def bliss_repl(asyncio_event_loop):
    yield start_bliss_repl


@pytest.fixture
def feed_cli_with_input(bliss_repl):
    """
    Create a Prompt, feed it with the given user input and return the CLI
    object.

    Inspired by python-prompt-toolkit/tests/test_cli.py
    """

    def f(
        text, check_line_ending=True, locals_dict=None, timeout=10, confirm_exit=False
    ):
        with gevent.timeout.Timeout(timeout):
            # If the given text doesn't end with a newline, the interface won't finish.
            if check_line_ending:
                assert text.endswith("\r")

            with bliss_repl(locals_dict, confirm_exit) as br:
                br.app.input.send_text(text)

                try:
                    result = br.app.run()
                except EOFError:
                    return None, None, None

                return result, br.app, br

    return f
