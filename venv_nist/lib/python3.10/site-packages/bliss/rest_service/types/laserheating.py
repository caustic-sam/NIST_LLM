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

LaserHeatingStates = ["READY", "ERROR", "UNKNOWN"]


class _PropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(LaserHeatingStates)]] = Field(None, read_only=True)
    exposure_time: Optional[float] = None
    background_mode: Optional[Literal["ON", "OFF", "ALWAYS"]] = None
    fit_wavelength: Optional[list[int]]  # Length(2, 2)
    current_calibration: Optional[str] = None


class _CallablesSchema(CallableSchema):
    measure: EmptyCallable


class LaserHeatingType(ObjectType):
    NAME = "laserheating"
    STATE_OK = [LaserHeatingStates[0]]

    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = LaserHeatingType
