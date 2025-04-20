# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Formatters for repl results"""

import logging
import functools
from bliss.shell.standard import info
from bliss.common.protocols import HasInfo
from bliss.physics.units import ur


@functools.singledispatch
def format_repl(arg):
    """Customization point format_repl for any types that need specific
    handling. This default implementation returns the __info__ if available.

    Usage:

    from bliss.shell.formatters.repl import format_repl

    @format_repl.register
    def _(arg: Foo):
        # Returns the representation of Foo
        return f"{arg.bar}"
    """
    return arg


@format_repl.register
def _(arg: HasInfo):
    """Specialization for types that implement the __info__ protocol."""

    class _InfoResult:
        def __pt_repr__(self):
            try:
                return info(arg)
            except Exception:
                logging.error("Error while formatting info", exc_info=True)
                return repr(arg)

    return _InfoResult()


@format_repl.register
def _(arg: ur.Quantity):
    """Specialization for Quantity"""

    class _QuantityResult:
        def __repr__(self):
            return f"{arg:~P}"  # short pretty formatting

    return _QuantityResult()
