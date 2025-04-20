#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.tango_attr_as_counter import TangoAttrAsCounterType

logger = logging.getLogger(__name__)


class TangoAttrAsCounter(ObjectMapping):
    TYPE = TangoAttrAsCounterType

    PROPERTY_MAP = {
        "name": HardwareProperty("name"),
        "attribute": HardwareProperty("attribute"),
        "value": HardwareProperty("value"),
        "unit": HardwareProperty("unit"),
    }

    CALLABLE_MAP = {
        "get_metadata": "get_metadata",
    }


Default = TangoAttrAsCounter
