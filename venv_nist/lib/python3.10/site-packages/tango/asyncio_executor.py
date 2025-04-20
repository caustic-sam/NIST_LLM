# -----------------------------------------------------------------------------
# This file is part of PyTango (http://pytango.rtfd.io)
#
# Copyright 2006-2012 CELLS / ALBA Synchrotron, Bellaterra, Spain
# Copyright 2013-2014 European Synchrotron Radiation Facility, Grenoble, France
#
# Distributed under the terms of the GNU Lesser General Public License,
# either version 3 of the License, or (at your option) any later version.
# See LICENSE.txt for more info.
# -----------------------------------------------------------------------------

# Imports
import functools
import asyncio
import inspect
import collections
import types


# Tango imports
from .green import AbstractExecutor

__all__ = ("AsyncioExecutor", "get_global_executor", "set_global_executor")


# Function removed from Python 3.11
# Taken from https://github.com/python/cpython/blob/3.10/Lib/asyncio/coroutines.py
# (without the _DEBUG part)
# Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
# 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022 Python Software Foundation;
# All Rights Reserved
def _coroutine(func):
    """Decorator to mark coroutines.
    If the coroutine is not yielded from before it is destroyed,
    an error message is logged.
    """
    if inspect.iscoroutinefunction(func):
        return func

    if inspect.isgeneratorfunction(func):
        coro = func
    else:

        @functools.wraps(func)
        def coro(*args, **kw):
            res = func(*args, **kw)
            if asyncio.isfuture(res) or inspect.isgenerator(res):
                res = yield from res
            else:
                # If 'res' is an awaitable, run it.
                try:
                    await_meth = res.__await__
                except AttributeError:
                    pass
                else:
                    if isinstance(res, collections.abc.Awaitable):
                        res = yield from await_meth()
            return res

    coro = types.coroutine(coro)
    wrapper = coro
    wrapper._is_coroutine = (
        asyncio.coroutines._is_coroutine
    )  # For iscoroutinefunction().
    return wrapper


# Global executor

_EXECUTOR = None


def get_global_executor():
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = AsyncioExecutor()
    return _EXECUTOR


def set_global_executor(executor):
    global _EXECUTOR
    _EXECUTOR = executor


# Asyncio executor


class AsyncioExecutor(AbstractExecutor):
    """Asyncio tango executor"""

    asynchronous = True
    default_wait = False

    def __init__(self, loop=None, subexecutor=None):
        super().__init__()
        if loop is None:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        self.loop = loop
        self.subexecutor = subexecutor

    def delegate(self, fn, *args, **kwargs):
        """Return the given operation as an asyncio future."""
        callback = functools.partial(fn, *args, **kwargs)
        coro = self.loop.run_in_executor(self.subexecutor, callback)
        return asyncio.ensure_future(coro)

    def access(self, accessor, timeout=None):
        """Return a result from an asyncio future."""
        if self.loop.is_running():
            raise RuntimeError("Loop is already running")
        coro = asyncio.wait_for(accessor, timeout)
        return self.loop.run_until_complete(coro)

    def submit(self, fn, *args, **kwargs):
        """Submit an operation"""
        corofn = _coroutine(lambda: fn(*args, **kwargs))
        return asyncio.run_coroutine_threadsafe(corofn(), self.loop)

    def execute(self, fn, *args, **kwargs):
        """Execute an operation and return the result."""
        if self.in_executor_context():
            corofn = _coroutine(lambda: fn(*args, **kwargs))
            return corofn()
        future = self.submit(fn, *args, **kwargs)
        return future.result()
