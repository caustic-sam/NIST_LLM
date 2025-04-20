# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Bliss main package

.. autosummary::
    :toctree:

    comm
    common
    config
    controllers
    physics
    scanning
    shell
    tango
    flint
"""
import sys as _sys
from . import release

__version__ = release.version

from bliss.common.greenlet_utils.gevent_monkey import (
    bliss_patch_all as _bliss_patch_all,
)

_bliss_patch_all()

from bliss.common.proxy import Proxy as _Proxy  # noqa: E402


_sessions = {}


def _get_current_session():
    """Return the last session that was registered

    In case of running as a library or as a single shell terminal,
    the last registered session must be the current one (see bliss/common/session.py,
    child sessions are not registered).

    For other cases -in particular for the web shell-, it is enough to
    replace this function with another one doing the Right Thing.
    """
    try:
        session_name = next(reversed(_sessions.keys()))
    except StopIteration:
        pass
    else:
        return _sessions[session_name]


current_session = _Proxy(lambda: _get_current_session())


def _get_setup_globals():
    return current_session.setup_globals


_sys.modules["bliss.setup_globals"] = _Proxy(_get_setup_globals)

from bliss.common.alias import MapWithAliases as _MapWithAliases  # noqa: E402

global_map = _MapWithAliases(current_session)
import atexit as _atexit  # noqa: E402

_atexit.register(global_map.clear)

from bliss.common.logtools import Log as _Log  # noqa: E402

# initialize the global logging
# it creates the "global" and "global.controllers" loggers
# (using BlissLoggers class with a default NullHandler handler)
# stdout_handler and beacon_handler not started here/yet
global_log = _Log(map=global_map)


# Bliss shell mode False indicates Bliss in running in library mode
_BLISS_SHELL_MODE = False


def set_bliss_shell_mode(mode=True):
    """
    Set Bliss shell mode
    """
    global _BLISS_SHELL_MODE
    _BLISS_SHELL_MODE = mode


def is_bliss_shell():
    """
    Tells if Bliss is running in shell or library mode
    """
    return _BLISS_SHELL_MODE
