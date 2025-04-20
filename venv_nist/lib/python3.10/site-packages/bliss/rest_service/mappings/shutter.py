from __future__ import annotations

import enum
import logging
from collections.abc import Callable

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from bliss.common.shutter import BaseShutterState
from bliss.controllers.tango_shutter import TangoShutterState

from ..types.shutter import ShutterType

logger = logging.getLogger(__name__)


_states_mapping = {
    BaseShutterState.OPEN: "OPEN",
    BaseShutterState.CLOSED: "CLOSED",
    BaseShutterState.FAULT: "FAULT",
    BaseShutterState.UNKNOWN: "UNKNOWN",
    # Duplication is needed cause TangoShutterState have totally different states
    # Even if it uses the same names
    TangoShutterState.OPEN: "OPEN",
    TangoShutterState.CLOSED: "CLOSED",
    TangoShutterState.FAULT: "FAULT",
    TangoShutterState.UNKNOWN: "UNKNOWN",
    TangoShutterState.MOVING: "MOVING",
    TangoShutterState.DISABLE: "DISABLED",
    TangoShutterState.RUNNING: "OPEN",  # Was automatically open by the machine
    TangoShutterState.STANDBY: "STANDBY",
}


class ShutterStateProperty(HardwareProperty):
    """Attribute mapping BLISS shutter state into blissterm names"""

    def __init__(self, name: str, getter: Callable | None = None):
        HardwareProperty.__init__(self, name, getter=getter)

    def string_to_state(self, value):
        for state_class in (BaseShutterState, TangoShutterState):
            # Check with enum names
            v = getattr(state_class, value, None)
            if v is not None:
                return v
            # Check with enum values
            try:
                return state_class(value)
            except ValueError:
                pass

        logger.warning(
            "BLISS shutter is exposing state as an unexpected value '%s' (%s).",
            value,
            type(value),
        )
        return BaseShutterState.UNKNOWN

    def translate_from(self, value):
        # TODO: Some shutters use their own state enums, so do a hacky string conversion
        # See https://gitlab.esrf.fr/bcu-vercors/ID26/id26/-/blob/master/id26/controllers/fast_shutter.py#L21
        # For this particular case we need to add AUTO to the Bliss BaseShutterState
        # https://gitlab.esrf.fr/bliss/bliss/-/blob/master/bliss/common/shutter.py#L51
        if isinstance(value, enum.Enum):
            for extra_value in ["AUTO", "CLOSED", "OPEN"]:
                if value.name == extra_value:
                    return extra_value

        if isinstance(value, str):
            value = self.string_to_state(value)
        try:
            state = _states_mapping[value]
        except KeyError:
            state = "UNKNOWN"
        return state


class Shutter(ObjectMapping):
    TYPE = ShutterType

    CALLABLE_MAP = {"open": "open", "close": "close", "toggle": "toggle"}

    def _get_status(self):
        bliss_obj = self._object
        if hasattr(bliss_obj, "_tango_status"):
            return bliss_obj._tango_status
        else:
            return ""

    def _get_open_text(self):
        return ""

    def _get_closed_text(self):
        return ""

    def _get_valid(self):
        return True

    PROPERTY_MAP = {
        "state": ShutterStateProperty("state"),
        "status": HardwareProperty("status", getter=_get_status),
        "open_text": HardwareProperty("open_text", getter=_get_open_text),
        "closed_text": HardwareProperty("closed_text", getter=_get_closed_text),
        "valid": HardwareProperty("valid", getter=_get_valid),
    }


Default = Shutter
