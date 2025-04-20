from functools import partial
from typing import Callable, Iterable, Optional


class NamespaceWrapper:
    """Namespace which delegates attribute getting and setting to a getter and setter method."""

    _PROPERTY_NAMES = ()

    def __new__(
        cls,
        property_names: Iterable[str],
        getter: Callable,
        setter: Optional[Callable] = None,
        property_decorator: Optional[Callable] = None,
    ):
        cls = type(cls.__name__, (cls,), {})
        if property_decorator is None:
            property_decorator = property
        property_names = tuple(sorted(property_names))
        cls._PROPERTY_NAMES = property_names
        for key in property_names:
            prop = property_decorator(partial(NamespaceWrapper._getter, key=key))
            if setter is not None:
                prop = prop.setter(partial(NamespaceWrapper._setter, key=key))
            setattr(cls, key, prop)
        return object.__new__(cls)

    def __init__(
        self,
        property_names: Iterable[str],
        getter: Callable,
        setter: Optional[Callable] = None,
        property_decorator: Optional[Callable] = None,
    ):
        self.__property_names = property_names
        self.__getter = getter
        self.__setter = setter

    def __str__(self) -> str:
        if self._PROPERTY_NAMES:
            return "NameSpace:\n " + "\n ".join(self._PROPERTY_NAMES)
        else:
            return "NameSpace: <empty>"

    def __info__(self) -> str:
        # For the Bliss shell
        return str(self)

    def _getter(self, key):
        return self.__getter(key)

    def _setter(self, value, key):
        return self.__setter(key, value)

    def get_content(self) -> str:
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
