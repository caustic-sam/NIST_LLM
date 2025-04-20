from typing import Any

from ..metadata.namespace_wrapper import NamespaceWrapper


def _wrap_dict(adict: dict) -> NamespaceWrapper:
    def getter(key: str) -> Any:
        item = adict.get(key, None)
        if item is None:
            raise AttributeError(key)
        if isinstance(item, dict):
            return _wrap_dict(item)
        return item

    def setter(key: str, value: Any) -> Any:
        adict[key] = value

    return NamespaceWrapper(
        property_names=list(adict),
        getter=getter,
        setter=setter,
    )


def test_namespace_str():
    adict = {"field1": 1, "group1": {"field2": 2, "group2": {}}}
    namespace = _wrap_dict(adict)
    assert str(namespace) == "NameSpace:\n field1\n group1"
    assert str(namespace.group1) == "NameSpace:\n field2\n group2"
    assert str(namespace.group1.group2) == "NameSpace: <empty>"
