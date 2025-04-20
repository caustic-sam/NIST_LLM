# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Some helpers to deal with prompt toolkit in bliss shell.
"""

from __future__ import annotations

import asyncio
import gevent
import typing
from bliss import is_bliss_shell
from prompt_toolkit.application import get_app_or_none

if typing.TYPE_CHECKING:
    from prompt_toolkit.input import Input


def isatty(input: Input) -> bool:
    """Return true if the input is a TTY"""
    stdin = getattr(input, "stdin", None)
    if stdin is None:
        return False
    if hasattr(stdin, "isatty"):
        return stdin.isatty()
    return False


def is_eval_greenlet() -> bool:
    """Return true if the current greenlet is the eval greenlet"""
    g = gevent.getcurrent()
    if not isinstance(g, gevent.Greenlet):
        return False
    eval_greenlet = g.spawn_tree_locals.get("eval_greenlet", None)
    is_eval_greenlet = eval_greenlet is g
    return is_eval_greenlet


def _event_loop_exists() -> bool:
    """True if there is an available loop.

    If None, no way to execute a prompt toolkit application.
    """
    loop = asyncio.get_event_loop_policy().get_event_loop()
    return not loop.is_closed()


def can_use_text_block() -> bool:
    """
    True if a text block can be used in the actual context.

    In the context of the BLISS shell, make sure it's an eval greenlet,
    and if an application is already there, that it is a
    TextBlockApplication.

    Outside of this context, make sure, at least there is no
    prompt-toolkit application and that it was called in a greenlet.
    """
    if not _event_loop_exists():
        return False

    g = gevent.getcurrent()
    if not isinstance(g, gevent.Greenlet):
        return False

    if not is_bliss_shell():
        app = get_app_or_none()
        return app is None

    eval_greenlet = g.spawn_tree_locals.get("eval_greenlet", None)
    is_eval_greenlet = eval_greenlet is g
    if not is_eval_greenlet:
        return False

    app = get_app_or_none()
    if app is None:
        return True

    from bliss.shell.pt.text_block_app import TextBlockApplication

    if isinstance(app, TextBlockApplication):
        return True

    return False


def is_textblock_context_greenlet() -> bool:
    """
    True if the actual greenlet is a context from the eval greenlet.

    This tag allow a greenlet to use the existing text_block which was not
    created by it.

    The tag `textblock_context_greenlet` have to be set manually to this greenlet.

    .. code-block:: python

        g = greenlet.spwan(...)
        g.spawn_tree_locals[id(g), "textblock_context_greenlet"] = True
    """
    g = gevent.getcurrent()
    if not isinstance(g, gevent.Greenlet):
        return False

    return g.spawn_tree_locals.get((id(g), "textblock_context_greenlet"), False)
