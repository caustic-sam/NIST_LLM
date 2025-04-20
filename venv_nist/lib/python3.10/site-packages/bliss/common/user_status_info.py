# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
This module is a central point for function to provide human readable
information to the user.

It can be used the following way:

.. code-block:: python

    from bliss.common.user_status_info import status_message
    import gevent
    with status_message() as update:
        update("Pif")
        gevent.sleep(1)
        update("Paf")
        gevent.sleep(1)
        update("Pouf")
        gevent.sleep(1)
        update("Toc")
        gevent.sleep(1)
        update("He fall down")
        gevent.sleep(1)
"""

from __future__ import annotations
import typing
import weakref
import contextlib
import abc
from contextlib import AbstractContextManager


_USER_MESSAGE_STATUS: weakref.WeakKeyDictionary[
    typing.Any, typing.Any
] = weakref.WeakKeyDictionary()
"""Store the user status"""

_LEVEL = 0
"""Store the user status"""


class UserStatusDisplay(abc.ABC):
    """Interface to handle the display of user status info in the fly."""

    @contextlib.contextmanager
    def use_display(self):
        """Context to setup the first use of the user status display"""
        ...

    @abc.abstractmethod
    def trigger_callback(self, *values: typing.Any):
        """Called when the user state info was changed"""
        ...


class PrintUserStatusDisplay(UserStatusDisplay):
    """Handle the display of user status info into a shell"""

    CLEAR_LINE = "\x1b[2K"
    """Ansi escape character to clear the entire line"""

    @contextlib.contextmanager
    def use_display(self):
        yield

    def trigger_callback(self, *values: typing.Any):
        """Called when the user state info was changed"""
        print(self.CLEAR_LINE, end="")
        print(*values, sep=", ", end="\r", flush=True)


_DISPLAY_CALLBACK: UserStatusDisplay | None = PrintUserStatusDisplay()


@contextlib.contextmanager
def status_message():
    """
    Helper to inform end user about a status message
    """
    global _LEVEL

    class K:
        pass

    key = K()

    def set(message):
        set_user_status_message(key, message)

    setup_display: AbstractContextManager[None] = contextlib.nullcontext()
    if _LEVEL == 0 and _DISPLAY_CALLBACK is not None:
        setup_display = _DISPLAY_CALLBACK.use_display()

    try:
        _LEVEL += 1
        with setup_display:
            yield set
    finally:
        remove_user_status_message(key)
        _LEVEL -= 1


def set_user_status_message(key, message):
    """
    Set a message to the end user about a status of something.
    example: when a scan is in pause during a refill.
    """
    _USER_MESSAGE_STATUS[key] = message
    _trigger_callback()


def remove_user_status_message(key):
    if _USER_MESSAGE_STATUS.pop(key, None) is not None:
        _trigger_callback()


def _trigger_callback():
    values = _USER_MESSAGE_STATUS.values()
    if _DISPLAY_CALLBACK is not None:
        _DISPLAY_CALLBACK.trigger_callback(*values)


def set_display_callback(display: UserStatusDisplay | None):
    """
    Change the global display of status information.

    If no display was set by the application, `PrintUserStatusDisplay`
    is instanciated at the first use of `status_message()`.
    """
    global _DISPLAY_CALLBACK
    assert _LEVEL == 0, "Hot switch of user status display not supported"
    _DISPLAY_CALLBACK = display


@contextlib.contextmanager
def callback():
    prev_display = _DISPLAY_CALLBACK
    try:
        yield set_display_callback
    finally:
        set_display_callback(prev_display)
