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

LaserStates = ["ON", "OFF", "ERROR", "UNKNOWN"]


class LaserPropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(LaserStates)]] = Field(None, read_only=True)
    power: Optional[float] = None


class LaserCallablesSchema(CallableSchema):
    on: EmptyCallable
    off: EmptyCallable


class LaserType(ObjectType):
    NAME = "laser"
    STATE_OK = [LaserStates[0]]

    PROPERTIES = LaserPropertiesSchema
    CALLABLES = LaserCallablesSchema


Default = LaserType
