#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.measurementgroup import MeasurementgroupType

logger = logging.getLogger(__name__)


class Measurementgroup(ObjectMapping):
    TYPE = MeasurementgroupType

    PROPERTY_MAP = {
        "available": HardwareProperty("available"),
        "disabled": HardwareProperty("disabled"),
    }

    CALLABLE_MAP = {
        "enable": "enable",
        "disable": "disable",
        "set_active": "set_active",
    }


Default = Measurementgroup
