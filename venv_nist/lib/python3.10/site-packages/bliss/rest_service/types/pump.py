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

PumpStates = ["ON", "OFF", "UNKNOWN", "ERROR"]


class PumpPropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(PumpStates)]] = Field(None, read_only=True)
    status: str = Field(None, read_only=True)
    pressure: float = Field(None, read_only=True)
    voltage: float = Field(None, read_only=True)
    current: float = Field(None, read_only=True)


class PumpCallablesSchema(CallableSchema):
    on: EmptyCallable
    off: EmptyCallable


class PumpType(ObjectType):
    NAME = "pump"
    STATE_OK = [PumpStates[0], PumpStates[1]]

    PROPERTIES = PumpPropertiesSchema
    CALLABLES = PumpCallablesSchema


Default = PumpType
