import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.actuator import ActuatorType

logger = logging.getLogger(__name__)


class Actuator(ObjectMapping):
    TYPE = ActuatorType

    PROPERTY_MAP = {"state": HardwareProperty("state")}

    CALLABLE_MAP = {"move_in": "open", "move_out": "close", "toggle": "toggle"}


Default = Actuator
