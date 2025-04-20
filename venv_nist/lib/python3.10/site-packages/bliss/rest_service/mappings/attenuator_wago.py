#!/usr/bin/env python
# -*- coding: utf-8 -*-

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.attenuator import (
    AttenuatorType,
    AttenuatorStates,
)


class Attenuator_Wago(ObjectMapping):
    TYPE = AttenuatorType

    def _get_state(self):
        return AttenuatorStates[0]

    def _get_factor(self):
        return self._object.factor()

    def _get_thickness(self):
        return self._object.thickness()

    PROPERTY_MAP = {
        "state": HardwareProperty("state", getter=_get_state),
        "factor": HardwareProperty("factor", getter=_get_factor),
        "thickness": HardwareProperty("thickness", getter=_get_thickness),
    }


Default = Attenuator_Wago
