# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Lima2 utility functions and classes."""

import logging

from typing_extensions import Any

_logger = logging.getLogger("bliss.ctrl.lima2")


def lazy_init(cls):
    """Class decorator for lazily initialized instances.

    When first instantiating the class, if the __init__ raises,
    a warning is emitted.

    On each subsequent attribute read/write or method call, __init__
    is attempted again with the same parameters, and if it raises
    again, the exception is propagated up. This makes the first
    initialization attempt fault tolerant.

    This behaviour assumes that the success or failure of __init__
    depends on external factors, e.g. a server being up. The
    parameters to __init__ cannot be modified between attempts.

    Example:
    ```
    x = 0  # Some external factor, here a global variable

    @lazy_init
    class LazyCoffee:
        def __init__(self):
            self.done = False
            if x == 0:
                raise ValueError

        def make(self):
            self.done = True

    >>> coffee = LazyCoffee()  # -> warning, no raise
    >>> coffee.make()          # -> ValueError raised
    >>> x = 1                  # Plug in the machine maybe
    >>> coffee.done
    False
    >>> coffee.make()          # -> ok
    >>> coffee.done
    True
    ```
    """

    class LazyInitWrapper(cls):
        """Wraps a class to make its first instantiation fault tolerant.

        The wrapper must inherit from `cls` to remain type-compatible with the
        wrapped class.
        """

        def __init__(self, *args, **kwargs):
            self._args = args
            self._kwargs = kwargs
            self._instance = None

            # Try to initialize now, warn on error
            try:
                self._try_init()
            except Exception as e:
                _logger.warning(
                    f"Instance of {cls.__name__} couldn't be initialized: {e}"
                )

        def _try_init(self) -> None:
            if self._instance is None:
                self._instance = cls(*self._args, **self._kwargs)

        def __setattr__(self, name: str, value: Any) -> None:
            """Before setting an attribute, try to initialize instance."""
            if name in ["__class__", "_args", "_kwargs", "_instance", "_try_init"]:
                return super().__setattr__(name, value)

            try:
                self._try_init()
            except Exception as e:
                raise ValueError(
                    f"Instance of {cls.__name__} is not initialized. See backtrace above."
                ) from e

            setattr(self._instance, name, value)

        def __getattribute__(self, name: str) -> Any:
            """Before accessing an attribute, try to initialize instance."""
            if name in ["__class__", "_args", "_kwargs", "_instance", "_try_init"]:
                return super().__getattribute__(name)

            try:
                self._try_init()
            except Exception as e:
                raise ValueError(
                    f"Instance of {cls.__name__} is not initialized. See backtrace above."
                ) from e

            return getattr(self._instance, name)

        def __repr__(self) -> str:
            try:
                self._try_init()
            except Exception as e:
                return (
                    f"Uninitialized {cls.__name__} object ({self.__class__.__name__}) "
                    f"(Reason: {repr(e)})"
                )
            return repr(self._instance)

    return LazyInitWrapper
