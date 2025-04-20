from __future__ import annotations
import logging
from typing import Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    CallableSchema,
    HardwareRefField,
)

logger = logging.getLogger(__name__)


class _PropertiesSchema(HardwareSchema):
    ref: Optional[str] = HardwareRefField()


class _CallablesSchema(CallableSchema):
    pass


class ObjectrefType(ObjectType):
    NAME = "objectref"
    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = ObjectrefType
