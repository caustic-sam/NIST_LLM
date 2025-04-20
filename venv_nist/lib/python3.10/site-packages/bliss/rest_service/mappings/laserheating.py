#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.laserheating import (
    LaserHeatingType,
    LaserHeatingStates,
)

logger = logging.getLogger(__name__)


class Laserheating(ObjectMapping):
    TYPE = LaserHeatingType

    def _get_state(self):
        return LaserHeatingStates[0]

    PROPERTY_MAP = {
        "state": HardwareProperty("state", getter=_get_state),
        "exposure_time": HardwareProperty("exposure_time"),
        "background_mode": HardwareProperty("background_mode"),
        "fit_wavelength": HardwareProperty("fit_wavelength"),
        "current_calibration": HardwareProperty("current_calibration"),
    }

    CALLABLE_MAP = {"measure": "measure"}


Default = Laserheating
