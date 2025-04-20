# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Helper to display a refreshable block of text.
"""

from __future__ import annotations
from collections.abc import Callable

import traceback
import logging
import gevent
import greenlet
import io
import contextlib

from prompt_toolkit.key_binding.key_bindings import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.filters import is_done
from prompt_toolkit.application import Application, AppSession
from prompt_toolkit.layout import ConditionalContainer
from prompt_toolkit.filters import Condition
from bliss.common.greenlet_utils import asyncio_gevent
from prompt_toolkit.layout import (
    FormattedTextControl,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit import ANSI, HTML
from prompt_toolkit import print_formatted_text
from prompt_toolkit.application import current
from . import default_style


_logger = logging.getLogger(__name__)


class _TextBlockKeyboardInterrupt(RuntimeError):
    """This exception is used as a work around to close the prompt.

    It sounds like gevent-asyncio is not properly handling the raise of such
    base exception in this context.
    """


class _TextBlockKeyboardInterruptGeventExit(gevent.GreenletExit):
    """This exception is used as a work around to close the prompt.

    It sounds like gevent-asyncio is not properly handling the raise of such
    base exception in this context.

    It inherite from `GreenletExit` to make sure it is not logged by gevent.
    """


class _CacheException:
    """Cache exception in order to not log them twice"""

    def __init__(self) -> None:
        self._cache: set[tuple] = set()

    def store(self, e: BaseException) -> bool:
        """Store an exception.

        Return:
            True if the exception is stored. False if the exception was
            not stored, because is was already there.
        """
        e_traceback = e.__traceback__
        e_msg = str(e)
        e_type = type(e)
        backtrace = "".join(traceback.format_tb(e_traceback, limit=1)[0])
        data = (e_msg, e_type, backtrace)
        if data in self._cache:
            return False
        self._cache.add(data)
        return True


class TextBlock:
    """Single block of text."""

    def __init__(
        self,
        render: (
            Callable[[], tuple[int, str | FormattedText | ANSI | HTML]] | None
        ) = None,
    ):
        self._render: Callable[[], tuple[int, str | FormattedText | ANSI | HTML]] = (
            render or self._default_render
        )
        self._cache_exception = _CacheException()
        self._block: tuple[int, str | FormattedText | ANSI | HTML] = 1, ""
        self._window_height: int
        self._window_height, _ = self._render()
        self._window = Window(
            FormattedTextControl(
                self._get_text,
                show_cursor=False,
            ),
            height=self._get_height,
            style="class:shell-move",
        )

    def _get_height(self) -> int:
        return self._window_height

    def _get_text(self) -> str | FormattedText | ANSI | HTML:
        try:
            self._window_height, text = self._render()
        except Exception as e:
            if self._cache_exception.store(e):
                _logger.error("Error during rendering", exc_info=True)
            return "Rendering error..."
        return text

    def _default_render(self) -> tuple[int, str | FormattedText | ANSI | HTML]:
        """Render if no render was defined in the constructor"""
        return self._block

    def _guess_height(self, text: str | FormattedText | ANSI | HTML) -> int:
        """Huess the height of a text block."""
        if isinstance(text, str):
            return text.count("\n") + 1
        if isinstance(text, FormattedText):
            return sum([t.count("\n") for t in text]) + 1
        if isinstance(text, ANSI):
            return text.value.count("\n") + 1
        if isinstance(text, HTML):
            return text.value.count("\n") + 1
        raise RuntimeError(f"Unsupported type {type(text)}")

    def set_text(
        self,
        text: str | FormattedText | ANSI | HTML,
        height: int | None = None,
    ):
        """Set text to display if no renderer was specified"""
        if height is None:
            height = self._guess_height(text)
        self._block = height, text
        self._render = self._default_render

    def set_render(
        self,
        render: Callable[[], tuple[int, str | FormattedText | ANSI | HTML]],
    ):
        """Set the render callback function to be used with the textblock"""
        self._render = render


class TextBlockApplication(Application):
    """Handle a refreshable text block.

    A user function (`render`) have to be defined to render the block content.
    It have to return the height of the block and the text content. The height
    can change dynalically, and the text can be one of `str` or prompt toolkit
    formatting classes `HTML`, `ANSI`, `FormattedText`.

    A `process` function can be defined, to process in a background stuffs,
    like moving motors. It can be one of a callable or a greenlet. If this
    function is defined, the application will terminated just after the
    termination of this processing function.

    The application can be aborted by the user with CTRL-C. If a `process`
    function was defined, it is first killed, then the application terminate
    by raising a `KeyboardInterrupt` exception.

    Here is a base code with only a renderer function:

    .. code-block:: python

        def render():
            # User function which returns height and content
            import time
            return 1, f"{time.time()}"

        app = TextBlockApplication(
            render=render,
            refresh_interval=0.3,
        )
        app.exec()

    Here is a base code with a background processing:

    .. code-block:: python

        def background():
            # Do some stuffs
            gevent.sleep(10.0)

        def render():
            import time
            return 1, f"{time.time()}"

        app = TextBlockApplication(
            render=render,
            refresh_interval=0.3,
        )
        app.exec(process=background)
    """

    _CHECK_GREENLET = True
    """Check that the execution is properly called in a greenlet.

    This mostly can be disabled for unittest.
    """

    def __init__(
        self,
        render: (
            Callable[[], tuple[int, str | FormattedText | ANSI | HTML]] | None
        ) = None,
        refresh_interval=0.3,
        style: Style | None = None,
        use_toolbar: bool = True,
        app_session: AppSession | None = None,
    ):
        self._handled_greenlet: gevent.Greenlet | None = None
        self._interruption_requested: int = 0
        self._initialized: gevent.event.Event = gevent.event.Event()
        self._was_render = gevent.event.Event()
        self._block = TextBlock(render=render)
        self._app_session = app_session
        self.interrupt_exception: type[
            BaseException
        ] = _TextBlockKeyboardInterruptGeventExit
        """Exception to be used to kill the process greenlet

        _TextBlockKeyboardInterruptGeventExit is used by default and converter
        to a normal KeyboardInterrupt in the end.
        """

        bottom_toolbar = ConditionalContainer(
            Window(
                FormattedTextControl(
                    " [ctrl-c] Abort", style="class:bottom-toolbar.text"
                ),
                style="class:bottom-toolbar",
                height=1,
            ),
            filter=Condition(lambda: not is_done() and not self.interruption_requested),
        )

        abort1_toolbar = ConditionalContainer(
            Window(
                FormattedTextControl(
                    " Aborting... Please wait", style="class:bottom-toolbar.text"
                ),
                style="class:bottom-toolbar class:aborting",
                height=1,
            ),
            filter=Condition(
                lambda: not is_done() and self._interruption_requested == 1
            ),
        )

        def abort2_label():
            return (
                " Aborting... It was requested %d times" % self._interruption_requested
            )

        abort2_toolbar = ConditionalContainer(
            Window(
                FormattedTextControl(abort2_label, style="class:bottom-toolbar.text"),
                style="class:bottom-toolbar class:aborting class:aborting2",
                height=1,
            ),
            filter=Condition(
                lambda: not is_done() and self._interruption_requested >= 2
            ),
        )

        if style is None:
            style = default_style.get_style()

        self._blocks = [self._block]
        self._windows = HSplit([self._block._window])

        if use_toolbar:
            layout = Layout(
                HSplit(
                    [
                        self._windows,
                        Window(height=1),
                        bottom_toolbar,
                        abort1_toolbar,
                        abort2_toolbar,
                    ]
                )
            )
        else:
            layout = Layout(
                HSplit(
                    [
                        self._windows,
                        Window(height=1),
                    ]
                )
            )

        app_session = self._app_session
        if app_session is None:
            app_session = current.get_app_session()

        Application.__init__(
            self,
            min_redraw_interval=0.05,
            refresh_interval=refresh_interval,
            layout=layout,
            mouse_support=False,
            key_bindings=self._create_bindings(),
            style=style,
            output=app_session.output,
            input=app_session.input,
        )

    def wait_render(self, timeout=None) -> bool:
        """Wait until the first render"""
        return self._was_render.wait(timeout=timeout)

    def _redraw(self, render_as_done: bool = False) -> None:
        # Overrided to capture the render signal
        try:
            return Application._redraw(self, render_as_done=render_as_done)
        finally:
            self._was_render.set()

    @property
    def interruption_requested(self) -> bool:
        """True if ctrl-c was pressed to request interruption"""
        return self._interruption_requested > 0

    def _create_bindings(self) -> KeyBindings:
        """
        Create the KeyBindings for a prompt application.
        """
        kbind = KeyBindings()

        @kbind.add("c-c")
        def _keyboard_interrupt(event: KeyPressEvent) -> None:
            "Abort when Control-C has been pressed."
            if self._handled_greenlet is None or self._handled_greenlet.ready():
                if not event.app.is_done:
                    # Make sure `self.exit` will be properly executed
                    self._initialized.wait()
                    event.app.exit(
                        exception=_TextBlockKeyboardInterrupt, style="class:aborting"
                    )
            else:
                self._interruption_requested += 1
                self.invalidate()
                self._handled_greenlet.kill(self.interrupt_exception, block=False)

        return kbind

    def _when_initialization_done(self):
        """Called when the application was properly initialized"""
        self._initialized.set()

    def _handled_greenlet_terminated(self, greenlet: greenlet.Greenlet):
        """Called when the handled greenlet was terminated"""
        # Make sure `self.exit` will be properly executed
        self._initialized.wait()
        if not self.is_done:
            if self._interruption_requested > 0:
                self.exit(exception=_TextBlockKeyboardInterrupt)
            else:
                self.exit()

    @contextlib.contextmanager
    def new_text_block(
        self,
        render: (
            Callable[[], tuple[int, str | FormattedText | ANSI | HTML]] | None
        ) = None,
    ):
        tb = TextBlock(render)
        try:
            self._blocks.append(tb)
            self._windows.children.append(tb._window)
            yield tb
        finally:
            self._blocks.remove(tb)
            self._windows.children.remove(tb._window)

    def first_text_block(self) -> TextBlock:
        return self._block

    @contextlib.contextmanager
    def _handle_stdout(self):
        """Handle special output for bliss stdout storage"""
        from bliss.shell.cli.bliss_output import BlissOutput

        output = self.output
        try:
            if isinstance(output, BlissOutput):
                output.in_text_block = True
            yield
        finally:
            if isinstance(output, BlissOutput):
                output.in_text_block = False
                # The content was ignored, so what we inject the last content
                text = io.StringIO()
                for i, b in enumerate(self._blocks):
                    if i != 0:
                        text.write("\n")
                    content = b._get_text()
                    print_formatted_text(content, file=text)
                output.append_stdout(text.getvalue())

            # Allow to flush the unitests with the termination of the application
            # See SimulatedOutput
            if hasattr(output, "_flush_app"):
                output._flush_app()

    def exec(self, process: gevent.Greenlet | Callable | None = None, *args, **kwargs):
        """
        Execute the application.

        Argument:
            process: If defined, the application will handle a processing.
                     This can be a greenlet or a callable (which will be
                     spawned with gevent).

        Raises:
            KeyboardInterrupt: If the application was aborted with ctrl-c
        """
        self._initialized.clear()
        self._was_render.clear()

        app_session = self._app_session or current.get_app_session()

        if process is None:
            self._handled_greenlet = None
        elif isinstance(process, gevent.Greenlet):
            if process.ready():
                return
            self._handled_greenlet = process
        elif callable(process):

            def patched_process():
                # Propagate the app session
                current._current_app_session.set(app_session)
                assert process is not None
                process()

            self._handled_greenlet = gevent.spawn(patched_process, *args, **kwargs)
            self._handled_greenlet.name = "text-block-app"
        else:
            raise TypeError(f"Type of 'process' {type(process)} unsupported")

        if self._handled_greenlet is not None:
            self._handled_greenlet.link(self._handled_greenlet_terminated)

        async def run():
            # Propagate app session
            current._current_app_session.set(app_session)
            await self.run_async(
                pre_run=self._when_initialization_done, handle_sigint=False
            )

        g_app = asyncio_gevent.future_to_greenlet(run())

        with self._handle_stdout():
            try:
                g_app.join()
            except BaseException:
                # Capture current greenlet exceptions (GreenletExit/timeout...)
                # We have to deal with the handled greenlet and the proper
                # termination of the application.
                if self._handled_greenlet is not None:
                    self._handled_greenlet.kill()
                self._handled_greenlet.join()
                g_app.join()
                raise

            try:
                g_app.get()
            except _TextBlockKeyboardInterrupt:
                # Normal termination requested by the user
                result = None
                if self._handled_greenlet is not None:
                    # This does not raise exception when it was killed
                    result = self._handled_greenlet.get()
                    self._handled_greenlet = None
                if isinstance(result, gevent.GreenletExit):
                    raise KeyboardInterrupt from result
                raise KeyboardInterrupt
            else:
                if self._handled_greenlet is not None:
                    try:
                        self._handled_greenlet.get()
                        self._handled_greenlet = None
                    except _TextBlockKeyboardInterruptGeventExit:
                        raise KeyboardInterrupt

    @contextlib.contextmanager
    def exec_context(self):
        """
        Execute the application in a context.

        This allows to do the processing inside the context directly.
        """
        # Assume the function was called from a greenlet so what it can self-kill itself
        g = gevent.getcurrent()
        if self._CHECK_GREENLET:
            if not isinstance(g, gevent.Greenlet):
                raise RuntimeError(
                    "exec_context can't be called outside a Grennet context"
                )
        self._handled_greenlet = g

        app_session = self._app_session or current.get_app_session()

        broken = False

        async def run():
            nonlocal broken
            # Propagate app session
            current._current_app_session.set(app_session)
            try:
                return await self.run_async(
                    pre_run=self._when_initialization_done, handle_sigint=False
                )
            except EOFError:
                broken = True
                raise
            except BaseException:
                _logger.error("Error while running a text block app", exc_info=True)
                raise

        g_app = None
        with self._handle_stdout():
            try:
                g_app = asyncio_gevent.future_to_greenlet(run())
                g_app.name = "textblock_display"
                try:
                    yield
                except _TextBlockKeyboardInterruptGeventExit:
                    # Propagate as KeyboardInterrupt instead
                    raise KeyboardInterrupt
            finally:
                self._initialized.wait()
                if not broken and not self.is_done:
                    self.exit()
                if g_app is not None:
                    g_app.join()
