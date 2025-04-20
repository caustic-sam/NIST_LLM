from __future__ import annotations
import logging
from typing import Any, Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
    EmptyCallable,
)

logger = logging.getLogger(__name__)

ProcedureStates = [
    "STANDBY",
    "DISABLED",
    "RUNNING",
    "ABORTING",
    "AWAITING_USER_INPUT",
    "UNKNOWN",
]


class ProcedurePropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(ProcedureStates)]] = Field(None, read_only=True)
    previous_run_state: Optional[str] = None
    previous_run_exception: Optional[str] = None
    parameters: dict[str, Any]


class ProcedureCallablesSchema(CallableSchema):
    start: EmptyCallable
    abort: EmptyCallable
    clear: EmptyCallable
    validate: Callable1Arg[dict[str, Any]]


class ProcedureType(ObjectType):
    NAME = "procedure"
    STATE_OK = ["STANDBY", "RUNNING"]

    PROPERTIES = ProcedurePropertiesSchema
    CALLABLES = ProcedureCallablesSchema


Default = ProcedureType
