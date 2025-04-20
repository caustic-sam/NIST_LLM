from collections.abc import Iterable, Mapping
from numbers import Number
from typing import Union
import datetime


def serialize_metadata(
    obj: Union[str, bytes, Number, None, Mapping, Iterable], **_recursive_info
) -> Union[str, None, dict]:
    """Serialize metadata for ICAT"""
    _recursive_info.setdefault("depth", 0)
    _recursive_info.setdefault("iterable_depth", 0)
    if isinstance(obj, str):
        return obj
    elif isinstance(obj, bytes):
        return obj.decode()
    elif isinstance(obj, Number):
        return str(obj)
    elif isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, Mapping):
        if _recursive_info["depth"]:
            raise TypeError(obj)
        _recursive_info["depth"] += 1
        return {
            serialize_metadata(k, **_recursive_info): serialize_metadata(
                v, **_recursive_info
            )
            for k, v in obj.items()
            if v is not None
        }
    elif isinstance(obj, Iterable):
        if _recursive_info["iterable_depth"] > 1:
            raise ValueError("")
        if all(isinstance(v, (str, bytes, Number)) or v is None for v in obj):
            sep = ","
        else:
            sep = " "
        _recursive_info["depth"] += 1
        _recursive_info["iterable_depth"] += 1
        return sep.join(
            [serialize_metadata(v, **_recursive_info) for v in obj if v is not None]
        )
    elif obj is None:
        return None
    else:
        raise TypeError(obj)
