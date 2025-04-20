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

GenericStates = ["ON", "OFF", "UNKNOWN", "ERROR"]


class GenericPropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(GenericStates)]] = Field(None, read_only=True)


class GenericCallablesSchema(CallableSchema):
    pass


class GenericType(ObjectType):
    NAME = "generic"
    STATE_OK = [GenericStates[0]]

    PROPERTIES = GenericPropertiesSchema
    CALLABLES = GenericCallablesSchema


Default = GenericType
