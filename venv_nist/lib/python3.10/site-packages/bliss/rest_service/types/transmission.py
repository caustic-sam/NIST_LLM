from __future__ import annotations
import logging
from typing import Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
)

logger = logging.getLogger(__name__)


class TransmissionPropertiesSchema(HardwareSchema):
    datafile: Optional[str] = Field(None, read_only=True)
    transmission_factor: Optional[str] = Field(None, read_only=True)


class TransmissionCallablesSchema(CallableSchema):
    set: Callable1Arg[float]
    get: Callable1Arg[float]


class TransmissionType(ObjectType):
    NAME = "transmission"

    PROPERTIES = TransmissionPropertiesSchema
    CALLABLES = TransmissionCallablesSchema


Default = TransmissionType
