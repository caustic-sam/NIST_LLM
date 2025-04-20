from __future__ import annotations
import logging
from typing import Optional
from pydantic import BaseModel

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    EmptyCallable,
)

logger = logging.getLogger(__name__)


class PdaValueUnitSchema(BaseModel):
    values: Optional[float] = Field(None, read_only=True)
    unit: Optional[str] = Field(None, read_only=True)


# class PdaValuesSchema(BaseModel):
#     channel: Optional[str] = Field(None, read_only=True)
#     wavelength = fields.Nested(PdaValueUnitSchema, many=False, metadata={"readOnly": True})
#     absorbance = fields.Nested(PdaValueUnitSchema, many=False, metadata={"readOnly": True})
#     bdanwidth = fields.Nested(PdaValueUnitSchema, many=False, metadata={"readOnly": True})
#     range = fields.Float = Field(None, read_only=True)
#     polarity: Optional[str] = Field(None, read_only=True)


class PdaDataSchema(BaseModel):
    xdata: list[float]
    ydata: list[float]


class PdaValuesSchema(BaseModel):
    channels: Optional[list[str]] = None
    w0: Optional[PdaDataSchema] = None
    w1: Optional[PdaDataSchema] = None
    w2: Optional[PdaDataSchema] = None
    w3: Optional[PdaDataSchema] = None


class ShimadzuPdaCallablesSchema(CallableSchema):
    connect_pda: EmptyCallable
    disconnect_pda: EmptyCallable
    read_wl: EmptyCallable
    read_all: EmptyCallable
    start_read: EmptyCallable
    stop_read: EmptyCallable


class ShimadzuPdaPropertiesSchema(HardwareSchema):
    data: Optional[PdaValuesSchema] = Field(None, read_only=True)


class ShimadzuPdaType(ObjectType):
    NAME = "shimadzupda"

    PROPERTIES = ShimadzuPdaPropertiesSchema
    CALLABLES = ShimadzuPdaCallablesSchema


Default = ShimadzuPdaType
