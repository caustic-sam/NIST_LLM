from __future__ import annotations
import logging
from typing import Any, Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
)

logger = logging.getLogger(__name__)

TangoDeviceStates = [
    "ON",
    "OFF",
    "CLOSE",
    "OPEN",
    "INSERT",
    "EXTRACT",
    "MOVING",
    "STANDBY",
    "FAULT",
    "INIT",
    "RUNNING",
    "ALARM",
    "DISABLE",
    "UNKNOWN",
]

TangoQualities = [
    "VALID",
    "INVALID",
    "ALARM",
    "CHANGING",
    "WARNING",
]

TangoDataTypes = [
    "DevVoid",
    "DevBoolean",
    "DevShort",
    "DevLong",
    "DevLong64",
    "DevUChar",
    "DevUShort",
    "DevULong",
    "DevULong64",
    "DevFloat",
    "DevDouble",
    "DevString",
    "DevVarBooleanArray",
    "DevVarDoubleArray",
    "DevVarFloatArray",
    "DevVarShortArray",
    "DevVarLongArray",
    "DevVarLong64Array",
    "DevVarCharArray",
    "DevVarStringArray",
    "DevVarUShortArray",
    "DevVarULongArray",
    "DevVarULong64Array",
    "DevEncoded",
    "DevVarEncodedArray",
    "DevVarLongStringArray",
    "DevVarDoubleStringArray",
    "DevState",
    "DevVarStateArray",
    "DevEnum",
    "DevPipeBlob",
    "DevFailed",
]


class _PropertiesSchema(HardwareSchema):
    # Device state

    state: Optional[Literal[tuple(TangoDeviceStates)]] = Field(None, read_only=True)
    status: Optional[str] = Field(None, read_only=True)

    # Attribute properties

    name: Optional[str] = Field(None, read_only=True)
    value: Optional[Any]
    # read_value
    # read_dim_x
    # read_dim_y
    # set_value
    # write_dim_x
    # write_dim_y
    quality: Literal[tuple(TangoQualities)]
    data_type: Literal[tuple(TangoDataTypes)]
    # data_format

    # Attribute config

    description: Optional[str] = None
    label: Optional[str] = None
    unit: Optional[str] = None
    # standard_unit: Optional[str] = None
    display_unit: Optional[str] = None
    format: Optional[str] = None
    # min_value: Optional[int | float] = None
    # max_value: Optional[int | float] = None
    # min_alarm: Optional[int | float] = None
    # max_alarm: Optional[int | float] = None
    # min_warning: Optional[int | float] = None
    # max_warning: Optional[int | float] = None
    # delta_val: Optional[int | float] = None
    # delta_t: Optional[int | float] = None
    # rel_change: Optional[int | float] = None
    # abs_change: Optional[int | float] = None
    # archive_rel_change: Optional[int | float] = None
    # archive_abs_change: Optional[int | float] = None
    # period: Optional[int | float] = None
    # archive_period: Optional[int | float] = None
    # writable
    # display_level


class _CallableSchema(CallableSchema):
    pass


class TangoAttrType(ObjectType):
    NAME = "tangoattr"
    STATE_OK = [
        "ON",
        "RUNNING",
        "STANDBY",
        "MOVING",
        "CLOSE",
        "OPEN",
        "INSERT",
        "EXTRACT",
    ]

    PROPERTIES = _PropertiesSchema

    CALLABLES = _CallableSchema


Default = TangoAttrType
