from __future__ import annotations
import logging
from typing import Optional
from pydantic import BaseModel

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
    EmptyCallable,
)

logger = logging.getLogger(__name__)


class VolumeParameters(BaseModel):
    volume: Optional[float] = None
    rate: Optional[float] = None


class PressureParameters(BaseModel):
    pmin: Optional[float] = None
    pmax: Optional[float] = None


class VialParameters(BaseModel):
    vial: Optional[float] = None
    vol: Optional[float] = None
    runtime: Optional[int] = None


class ShimadzuCBM20PumpSchema(BaseModel):
    on: Optional[bool]
    flow_rate: Optional[float] = None
    min_pressure: Optional[float] = None
    max_pressure: Optional[float] = None
    port: Optional[str] = None


class ShimadzuCBM20AutoSamplerSchema(BaseModel):
    on: Optional[bool]
    temperature: Optional[float] = None
    temperature_setpoint: Optional[float] = None


class ShimadzuCBM20AutoPurgeSchema(BaseModel):
    on: Optional[bool]


class ShimadzuCBM20StateSchema(BaseModel):
    pump: Optional[ShimadzuCBM20PumpSchema] = Field(None, read_only=True)
    auto_purge: Optional[ShimadzuCBM20AutoPurgeSchema] = Field(None, read_only=True)
    auto_sampler: Optional[ShimadzuCBM20AutoSamplerSchema] = Field(None, read_only=True)
    inject_from_vial: Optional[VialParameters] = Field(None, read_only=True)
    system_state: Optional[int] = Field(None, read_only=True)


class ShimadzuCBM20CallablesSchema(CallableSchema):
    connect_cbm20: EmptyCallable
    disconnect_cbm20: EmptyCallable
    start_pump: EmptyCallable
    stop_pump: EmptyCallable
    set_pump_max_pressure: Callable1Arg[float]
    set_pump_min_pressure: Callable1Arg[float]
    set_pump_flow: Callable1Arg[float]
    set_pump_flow_threshold: Callable1Arg[float]
    set_flow_mode: Callable1Arg[str]
    select_solenoid_valve: Callable1Arg[str]
    start_auto_purge: EmptyCallable
    stop_auto_purge: EmptyCallable
    inject_from_vial: Callable1Arg[VialParameters]
    stop_inject: EmptyCallable
    set_auto_sampler_temp: Callable1Arg[float]
    pump_from_port: Callable1Arg[str]
    enable_auto_sampler: EmptyCallable
    disable_auto_sampler: EmptyCallable


class DataValueSchema(BaseModel):
    xdata: list[float]
    ydata: list[float]


class ShimadzuCBM20DataSchema(BaseModel):
    channels: Optional[list[str]]
    pressure: Optional[list[str]]
    flow_rate: Optional[list[str]]


class ShimadzuCBM20PropertiesSchema(HardwareSchema):
    state: Optional[ShimadzuCBM20StateSchema] = Field(None, read_only=True)
    data: Optional[ShimadzuCBM20DataSchema] = Field(None, read_only=True)


class ShimadzuCBM20Type(ObjectType):
    NAME = "shimadzucbm20"

    PROPERTIES = ShimadzuCBM20PropertiesSchema
    CALLABLES = ShimadzuCBM20CallablesSchema


Default = ShimadzuCBM20Type
