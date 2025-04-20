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

ServiceStates = [
    "STOPPED",
    # The process has been stopped due to a stop request or has never been started.
    "STARTING",
    # The process is starting due to a start request.
    "RUNNING",
    # The process is running.
    "BACKOFF",
    # The process entered the STARTING state but subsequently exited too quickly
    # to move to the RUNNING state.
    "STOPPING",
    # The process is stopping due to a stop request.
    "EXITED",
    # The process exited from the RUNNING state (expectedly or unexpectedly).
    "FATAL",
    # The process could not be started successfully.
    "UNKNOWN",
    # The process is in an unknown state (supervisord programming error).
]
"""See http://supervisord.org/subprocess.html#process-states"""


class ServicePropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(ServiceStates)]] = Field(None, read_only=True)


class ServiceCallablesSchema(CallableSchema):
    start: EmptyCallable
    stop: EmptyCallable
    restart: EmptyCallable


class ServiceType(ObjectType):
    NAME = "service"
    STATE_OK = ["STARTING", "RUNNING"]

    PROPERTIES = ServicePropertiesSchema
    CALLABLES = ServiceCallablesSchema


Default = ServiceType
