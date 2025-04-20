import logging
from typing import Literal

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    EmptyCallable,
)


logger = logging.getLogger(__name__)

ActuatorStatues = ["IN", "OUT", "UNKNOWN", "ERROR"]


class ActuatorPropertiesSchema(HardwareSchema):
    state: Literal[tuple(ActuatorStatues)] = Field("UNKNOWN", read_only=True)


class ActuatorCallablesSchema(CallableSchema):
    move_in: EmptyCallable
    move_out: EmptyCallable
    toggle: EmptyCallable


class ActuatorType(ObjectType):
    NAME = "actuator"
    STATE_OK = [ActuatorStatues[0], ActuatorStatues[1]]

    PROPERTIES = ActuatorPropertiesSchema
    CALLABLES = ActuatorCallablesSchema


Default = ActuatorType
