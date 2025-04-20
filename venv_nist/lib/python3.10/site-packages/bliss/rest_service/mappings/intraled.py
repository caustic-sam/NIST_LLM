import logging


from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.light import LightType, LightStates

logger = logging.getLogger(__name__)


class StateProperty(HardwareProperty):
    def translate_from(self, value):
        for s in LightStates:
            if s == value.upper():
                return s

        return ["UNKNOWN"]


class Intraled(ObjectMapping):
    TYPE = LightType

    PROPERTY_MAP = {
        "intensity": HardwareProperty("intensity"),
        "temperature": HardwareProperty("temperature"),
        "state": StateProperty("modus"),
    }


Default = Intraled
