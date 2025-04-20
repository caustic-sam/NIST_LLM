import logging
from typing import Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
)

logger = logging.getLogger(__name__)

LightStates = ["ON", "OFF", "STANDBY", "ERROR"]


class _PropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(LightStates)]] = Field(None, read_only=True)
    temperature: Optional[float] = Field(None, read_only=True)
    intensity: Optional[float] = None


class _CallablesSchema(CallableSchema):
    pass


class LightType(ObjectType):
    NAME = "light"
    STATE_OK = [LightStates[0], LightStates[1], LightStates[2]]

    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = LightType
