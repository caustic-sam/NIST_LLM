import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.multiposition import (
    MultipositionType,
    MultipositionStates,
)

logger = logging.getLogger(__name__)


class PositionsProperty(HardwareProperty):
    def translate_from(self, value):
        positions = []
        for line in value:
            target = [
                {
                    "object": t["axis"].name,
                    "destination": t["destination"],
                    "tolerance": t["tolerance"],
                }
                for t in line["target"]
            ]
            p = {
                "position": line["label"],
                "description": line["description"],
                "target": target,
            }
            positions.append(p)

        return positions


class StateProperty(HardwareProperty):
    def translate_from(self, value):
        for s in MultipositionStates:
            if value == s:
                return s


class Multiposition(ObjectMapping):
    TYPE = MultipositionType

    PROPERTY_MAP = {
        "position": HardwareProperty("position"),
        "positions": PositionsProperty("positions_list"),
        "state": StateProperty("state"),
    }

    CALLABLE_MAP = {"stop": "stop"}

    def _call_move(self, value):
        logger.debug(f"_call_move multiposition {value}")
        self._object.move(value, wait=False)


Default = Multiposition
