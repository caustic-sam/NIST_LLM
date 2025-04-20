from __future__ import annotations
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

ValveStates = ["OPEN", "CLOSED", "UNKNOWN", "FAULT"]


class ValvePropertiesSchema(HardwareSchema):
    state: Literal[tuple(ValveStates)] = Field("UNKNOWN", read_only=True)
    status: Optional[str] = Field(None, read_only=True)


class ValveCallablesSchema(CallableSchema):
    open: EmptyCallable
    close: EmptyCallable
    reset: EmptyCallable


class ValveType(ObjectType):
    NAME = "valve"
    STATE_OK = [ValveStates[0], ValveStates[1]]

    PROPERTIES = ValvePropertiesSchema
    CALLABLES = ValveCallablesSchema


Default = ValveType
