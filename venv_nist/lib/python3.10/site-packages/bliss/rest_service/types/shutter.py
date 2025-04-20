import logging
from typing import Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    EmptyCallable,
)

logger = logging.getLogger(__name__)

ShutterStates = [
    "OPEN",
    "CLOSED",
    "AUTO",
    "MOVING",
    "DISABLED",
    "STANDBY",
    "FAULT",
    "UNKNOWN",
]


class ShutterPropertiesSchema(HardwareSchema):
    state: Literal[tuple(ShutterStates)] = Field("UNKNOWN", read_only=True)
    status: Optional[str] = Field(None, read_only=True)
    valid: Optional[bool] = None
    open_text: Optional[str] = Field(None, read_only=True)
    closed_text: Optional[str] = Field(None, read_only=True)


class ShutterCallablesSchema(CallableSchema):
    open: EmptyCallable
    close: EmptyCallable
    toggle: EmptyCallable
    reset: EmptyCallable


class ShutterType(ObjectType):
    NAME = "shutter"
    STATE_OK = [ShutterStates[0], ShutterStates[1]]

    PROPERTIES = ShutterPropertiesSchema
    CALLABLES = ShutterCallablesSchema


Default = ShutterType
