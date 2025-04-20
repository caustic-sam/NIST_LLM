import logging

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
    EmptyCallable,
)

logger = logging.getLogger(__name__)


class _PropertiesSchema(HardwareSchema):
    available: list[str] = Field(
        description="A list of available counters (including those enabled)"
    )
    disabled: list[str] = Field(description="A list of disabled counters")


class _CallablesSchema(CallableSchema):
    enable: Callable1Arg[str]
    disable: Callable1Arg[str]
    set_active: EmptyCallable


class MeasurementgroupType(ObjectType):
    NAME = "measurementgroup"
    STATE_OK = []

    PROPERTIES = _PropertiesSchema
    CALLABLES = _CallablesSchema


Default = MeasurementgroupType
