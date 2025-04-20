#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import math
import typing

from bliss.common.axis import Axis, AxisState
from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
    CouldRaiseException,
)
from ..types.motor import MotorType, MotorStates


logger = logging.getLogger(__name__)


class LimitsProperty(HardwareProperty):
    def translate_from(self, value):
        if value is None:
            return [None, None]

        value = list(value)
        for i, v in enumerate(value):
            if math.isinf(v):
                if v < 0:
                    value[i] = -999999999
                else:
                    value[i] = 999999999
        if value[0] > value[1]:
            # BLISS returns the biggest value first, when the sign is negative
            return value[::-1]
        return value


# Convert BLISS state into Daiquiri state.
# Other state names already matches.
STATE_MAPPING = {
    "LIMPOS": "HIGHLIMIT",
    "LIMNEG": "LOWLIMIT",
}


class StateProperty(HardwareProperty):
    def translate_from(self, value: AxisState | None):
        if value is None:
            return ["UNKNOWN"]

        states = []
        for name in value.current_states_names:
            normalized = STATE_MAPPING.get(name, name)
            if normalized in MotorStates:
                states.append(normalized)
            else:
                desc = value._state_desc[name]
                states.append(f"_{name}:{desc}")

        if len(states):
            return states

        # It's "not READY"
        return []


class PositionProperty(HardwareProperty):
    def translate_from(self, value):
        if value is None:
            return None

        if math.isnan(value):
            return None

        return value


class NoneIfNotImplementedProperty(CouldRaiseException):
    """Acceleration and velocity can not be exposed in case of a
    CalcController.
    """

    def handleExceptionAsValue(self, exception: Exception) -> typing.Any:
        """Return the value to use, else raise the exception."""
        if isinstance(exception, NotImplementedError):
            return None
        raise exception


class Motor(ObjectMapping[Axis]):
    TYPE = MotorType

    PROPERTY_MAP = {
        "position": PositionProperty("position"),
        "target": PositionProperty("_set_position"),
        "tolerance": HardwareProperty("tolerance"),
        "acceleration": NoneIfNotImplementedProperty("acceleration"),
        "velocity": NoneIfNotImplementedProperty("velocity"),
        "limits": LimitsProperty("limits"),
        "state": StateProperty("state"),
        "unit": HardwareProperty("unit"),
        "offset": HardwareProperty("offset"),
        "sign": HardwareProperty("sign"),
        "display_digits": HardwareProperty("display_digits"),
    }

    CALLABLE_MAP = {"stop": "stop", "wait": "wait_move"}

    def check_online(self) -> bool:
        return not self._object.disabled

    def _call_move(self, value, **kwargs):
        logger.debug("_call_move %s %s %s", self.name, value, kwargs)
        self._object.move(value, wait=True)

    def _call_rmove(self, value, **kwargs):
        logger.debug("_call_rmove %s %s %s", self.name, value, kwargs)
        self._object.move(value, wait=True, relative=True)


Default = Motor
