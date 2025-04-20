# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from functools import partial
from typing import Optional
from collections.abc import Iterable, Callable
from bliss.common.utils import autocomplete_property


class NamespaceWrapper(object):
    """Namespace which delegates attribute getting and setting to a getter and setter method."""

    def __new__(
        cls,
        property_names: Iterable[str],
        getter: Callable,
        setter: Optional[Callable] = None,
        autocomplete: bool = False,
    ):
        cls = type(cls.__name__, (cls,), {})
        for key in property_names:
            if autocomplete:
                decorator = autocomplete_property
            else:
                decorator = property
            prop = decorator(partial(NamespaceWrapper._getter, key=key))
            if setter is not None:
                prop = prop.setter(partial(NamespaceWrapper._setter, key=key))
            setattr(cls, key, prop)
        return object.__new__(cls)

    def __init__(
        self,
        property_names: Iterable[str],
        getter: Callable,
        setter: Optional[Callable] = None,
        autocomplete: bool = False,
    ):
        self.__property_names = property_names
        self.__getter = getter
        self.__setter = setter

    def _getter(self, key):
        return self.__getter(key)

    def _setter(self, value, key):
        return self.__setter(key, value)

    def __info__(self) -> str:
        if not self.__property_names:
            return "Namespace is empty"
        res = "Namespace contains:\n"
        max_len = max(len(s) for s in self.__property_names)
        key_fmt = f".%-{max_len}s"
        for key in self.__property_names:
            val = self._getter(key)
            if val:
                res += (key_fmt % key) + f" = {val!r}\n"
            else:
                res += (key_fmt % key) + "\n"
        return res
