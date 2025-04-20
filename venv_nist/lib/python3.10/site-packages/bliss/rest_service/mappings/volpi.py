#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.light import LightType, LightStates

logger = logging.getLogger(__name__)


class Volpi(ObjectMapping):
    TYPE = LightType

    def _get_state(self):
        return LightStates[0]

    def _get_temperature(self):
        return 0

    PROPERTY_MAP = {
        "intensity": HardwareProperty("intensity"),
        "state": HardwareProperty("state", getter=_get_state),
        "temperature": HardwareProperty("temperature", getter=_get_temperature),
    }


Default = Volpi
