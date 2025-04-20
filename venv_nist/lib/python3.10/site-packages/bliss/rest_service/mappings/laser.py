#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.laser import LaserType

logger = logging.getLogger(__name__)


class Laser(ObjectMapping):
    TYPE = LaserType

    PROPERTY_MAP = {
        "power": HardwareProperty("power"),
        "state": HardwareProperty("state"),
    }

    CALLABLE_MAP = {"on": "on", "off": "off"}


Default = Laser
