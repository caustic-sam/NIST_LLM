# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Converter to be able to serialize some BLISS object with JSON.

It creates a language which can be deserialized in the client
side.
"""

import numpy
import numbers
import tblib
import typing
from bliss.physics.units import ur
from bliss import _get_current_session
from bliss.scanning.scan import Scan


def bliss_to_jsonready(content: typing.Any) -> typing.Any:
    if isinstance(content, list):
        return [bliss_to_jsonready(o) for o in content]
    if isinstance(content, dict):
        return {k: bliss_to_jsonready(v) for k, v in content.items()}
    return obj_to_jsonready(content)


def obj_to_jsonready(obj: typing.Any) -> typing.Any:
    """
    Convert a single object into a valid json serializable
    """
    if isinstance(obj, numbers.Real):
        if not numpy.isfinite(obj):
            # This are not supported by json
            if numpy.isnan(obj):
                return {"__type__": "nan"}
            if numpy.isneginf(obj):
                return {"__type__": "neginf"}
            if numpy.isposinf(obj):
                return {"__type__": "posinf"}
            assert False, f"Unexpected {obj}"

    if isinstance(obj, Scan):
        return {
            "__type__": "scan",
            "key": obj._scan_data.key,
        }

    if isinstance(obj, ur.Quantity):
        return {
            "__type__": "quantity",
            "scalar": obj_to_jsonready(obj.magnitude),
            "unit": f"{obj.units:~}",
        }

    if isinstance(obj, BaseException):
        return {
            "__type__": "exception",
            "class": type(obj).__name__,
            "message": str(obj),
            "traceback": tblib.Traceback(obj.__traceback__).to_dict(),
        }

    if hasattr(obj, "name"):
        return {"__type__": "object", "name": obj.name}

    return obj


def bliss_from_jsonready(content: typing.Any) -> typing.Any:
    """
    Convert a single object into a valid json serializable
    """
    if isinstance(content, list):
        return [bliss_from_jsonready(o) for o in content]

    if not isinstance(content, dict):
        return content

    bliss_type = content.get("__type__")
    if bliss_type is None:
        return {k: bliss_from_jsonready(v) for k, v in content.items()}

    if bliss_type == "nan":
        return float("nan")

    if bliss_type == "neginf":
        return float("-inf")

    if bliss_type == "posinf":
        return float("inf")

    if bliss_type == "scan":
        # Actually it's not a use case
        # No need to wast time on it
        raise RuntimeError("'scan' deserialiwion is not allowed")

    if bliss_type == "quantity":
        return ur.Quantity(
            bliss_from_jsonready(content.get("scalar")),
            content.get("unit"),
        )

    if bliss_type == "object":
        session = _get_current_session()
        config = session.env_dict["config"]
        name = content.get("name")
        obj = config.get(name)
        return obj

    if bliss_type == "exception":
        # Actually it's not a use case
        # No need to wast time on it
        raise RuntimeError("'excepyion' deserialiwion is not allowed")

    return content
