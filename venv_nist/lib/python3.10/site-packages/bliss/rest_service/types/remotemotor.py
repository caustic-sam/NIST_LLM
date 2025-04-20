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

RemoteMotorStates = ["ON"]


class RemotemotorPropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(RemoteMotorStates)]] = Field(None, read_only=True)
    resolution: Optional[int] = None


class RemotemotorCallablesSchema(CallableSchema):
    enable: EmptyCallable
    disable: EmptyCallable


class RemotemotorType(ObjectType):
    NAME = "remotemotor"
    STATE_OK = [RemoteMotorStates[0]]

    PROPERTIES = RemotemotorPropertiesSchema
    CALLABLES = RemotemotorCallablesSchema


Default = RemotemotorType
