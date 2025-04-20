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

FrontendStates = ["OPEN", "RUNNING", "STANDBY", "CLOSED", "UNKNOWN", "FAULT"]
FrontendItlkStates = ["ON", "FAULT"]


class FrontendPropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(FrontendStates)]] = Field(None, read_only=True)
    status: Optional[str] = Field(None, read_only=True)

    automatic: Optional[bool] = Field(None, read_only=True)
    frontend: Optional[str] = Field(None, read_only=True)

    current: Optional[float] = Field(None, read_only=True)
    refill: Optional[float] = Field(None, read_only=True)
    mode: Optional[str] = Field(None, read_only=True)
    message: Optional[str] = Field(None, read_only=True)

    feitlk: Optional[Literal[tuple(FrontendItlkStates)]] = Field(None, read_only=True)
    pssitlk: Optional[Literal[tuple(FrontendItlkStates)]] = Field(None, read_only=True)
    expitlk: Optional[Literal[tuple(FrontendItlkStates)]] = Field(None, read_only=True)


class FrontendCallablesSchema(CallableSchema):
    open: EmptyCallable
    close: EmptyCallable
    reset: EmptyCallable


class FrontendType(ObjectType):
    NAME = "frontend"
    STATE_OK = [FrontendStates[0], FrontendStates[1]]

    PROPERTIES = FrontendPropertiesSchema
    CALLABLES = FrontendCallablesSchema


Default = FrontendType
