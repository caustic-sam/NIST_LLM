from __future__ import annotations
import logging

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    CallableSchema,
)

logger = logging.getLogger(__name__)


class _PropertiesSchema(HardwareSchema):
    pass


class _CallablesSchema(CallableSchema):
    pass


class UnknownType(ObjectType):
    NAME = "unknown"
    STATE_OK = []

    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = UnknownType
