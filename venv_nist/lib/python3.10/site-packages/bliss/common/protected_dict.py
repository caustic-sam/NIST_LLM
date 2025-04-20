# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import functools
import inspect

"""
The aim is to implement a dict where some keys are protected from simple
overwriting.
"""


class ProtectedDictType(type):
    class DictProxyClass(object):
        def __init__(self, wrapped):
            """
            Arguments:
                wrapped: dict
                protected_keys: set of protected keys
            """
            self._wrapped = wrapped
            self._protected_keys = set()

        def proxy(self, attr, *args):
            return getattr(self._wrapped, attr)(*args)

        def proxy_property(self, attr):
            return getattr(self._wrapped, attr)

        def setitem(self, key, value):
            if key not in self._protected_keys:
                self._wrapped[key] = value
            else:
                if key in self._wrapped:
                    if self._wrapped[key] is not value:
                        raise RuntimeError(
                            f"{key} is protected and cannot be modified!"
                        )
                else:
                    self._wrapped[key] = value

    def repartial(func, parameter):
        @functools.wraps(func)
        def wrapped(self, *args, **kw):
            return func(self, parameter, *args, **kw)

        return wrapped

    def __new__(cls, name, bases, attrs):
        return super(ProtectedDictType, cls).__new__(
            cls, name, (ProtectedDictType.DictProxyClass,) + bases, attrs
        )

    def __init__(cls, name, bases, attrs):
        setattr(cls, "__setitem__", cls.setitem)
        untouched = (
            "__new__",
            "__init__",
            "__class__",
            "__setitem__",
            "__setattr__",
            "__getattribute__",
        )
        for attributeName in dir(dict()):
            if (
                callable(getattr(dict(), attributeName))
                and attributeName not in untouched
            ):
                setattr(
                    cls,
                    attributeName,
                    ProtectedDictType.repartial(cls.proxy, attributeName),
                )
            elif attributeName not in untouched:
                setattr(
                    cls,
                    attributeName,
                    property(
                        ProtectedDictType.repartial(cls.proxy_property, attributeName)
                    ),
                )


class ProtectedDict(dict, metaclass=ProtectedDictType):
    def _protect(self, to_be_protected, global_vars_dict=None):
        if isinstance(to_be_protected, str):
            to_be_protected = (to_be_protected,)
        for var_name in to_be_protected:
            if global_vars_dict:
                if var_name not in global_vars_dict:
                    if var_name not in self.wrapped_dict:
                        assert (
                            False
                        ), f"{var_name} variable does not exist in this context globals"
            self._protected_keys.add(var_name)

    def protect(self, to_be_protected):
        """User function to add a key or a list/set of keys to the inventory of protected keys"""
        assert (
            isinstance(to_be_protected, str)
            or isinstance(to_be_protected, set)
            or isinstance(to_be_protected, list)
        )
        caller_frame = inspect.currentframe().f_back
        global_vars_dict = caller_frame.f_globals
        self._protect(to_be_protected, global_vars_dict=global_vars_dict)

    def unprotect(self, key):
        """remove a key from the inventory of protected keys"""
        assert key in self._protected_keys
        self._protected_keys.remove(key)

    def is_protected(self, key):
        return key in self._protected_keys

    @property
    def wrapped_dict(self):
        return self._wrapped
