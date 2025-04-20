from __future__ import annotations
import logging
from typing import Any, Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
)

logger = logging.getLogger(__name__)

ProcessorStates = ["READY", "PROCESSING", "OFFLINE", "UNKNOWN"]


class ProcessorPropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(ProcessorStates)]] = Field(None, read_only=True)
    state_ok: Optional[bool] = None
    enabled: Optional[bool] = None


class ProcessorCallablesSchema(CallableSchema):
    reprocess: Callable1Arg[dict[str, Any]]


class ProcessorType(ObjectType):
    NAME = "processor"
    STATE_OK = [ProcessorStates[0], ProcessorStates[1]]

    PROPERTIES = ProcessorPropertiesSchema
    CALLABLES = ProcessorCallablesSchema


Default = ProcessorType
