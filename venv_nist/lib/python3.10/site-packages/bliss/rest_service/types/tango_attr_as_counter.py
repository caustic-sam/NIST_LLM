from __future__ import annotations
import logging
from typing import Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    EmptyCallable,
)

logger = logging.getLogger(__name__)


class _PropertiesSchema(HardwareSchema):
    name: Optional[str] = Field(None, read_only=True)
    attribute: Optional[str] = Field(None, read_only=True)
    value: Optional[float] = None
    unit: Optional[str] = Field(None, read_only=True)


class _CallablesSchema(CallableSchema):
    get_metadata: EmptyCallable


class TangoAttrAsCounterType(ObjectType):
    NAME = "tango_attr_as_counter"

    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = TangoAttrAsCounterType
