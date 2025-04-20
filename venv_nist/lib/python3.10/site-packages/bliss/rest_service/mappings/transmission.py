#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.transmission import TransmissionType

logger = logging.getLogger(__name__)


class Transmission(ObjectMapping):
    TYPE = TransmissionType

    PROPERTY_MAP = {
        "datafile": HardwareProperty("datafile"),
        "transmission_factor": HardwareProperty("get"),
    }

    CALLABLE_MAP = {"get": "get", "set": "set"}


Default = Transmission
