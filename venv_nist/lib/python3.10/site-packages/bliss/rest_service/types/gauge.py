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

GaugeStates = ["ON", "OFF", "UNKNOWN", "ERROR"]


class GaugePropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(GaugeStates)]] = Field(None, read_only=True)
    status: Optional[str] = Field(None, read_only=True)
    pressure: Optional[float] = Field(None, read_only=True)


class GaugeCallablesSchema(CallableSchema):
    on: EmptyCallable
    off: EmptyCallable


class GaugeType(ObjectType):
    NAME = "gauge"
    STATE_OK = [GaugeStates[0], GaugeStates[1]]

    PROPERTIES = GaugePropertiesSchema
    CALLABLES = GaugeCallablesSchema


Default = GaugeType
