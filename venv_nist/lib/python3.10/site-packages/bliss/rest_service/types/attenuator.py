from __future__ import annotations
import logging
from typing import Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
)

logger = logging.getLogger(__name__)

AttenuatorStates = ("ON", "ERROR")


class _PropertiesSchema(HardwareSchema):
    state: Literal[AttenuatorStates] = Field(None, read_only=True)
    factor: Optional[int] = Field(None, read_only=True)
    thickness: Optional[float] = Field(None, read_only=True)


class _CallablesSchema(CallableSchema):
    pass


class AttenuatorType(ObjectType):
    NAME = "attenuator"
    STATE_OK = [AttenuatorStates[0]]
    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = AttenuatorType
