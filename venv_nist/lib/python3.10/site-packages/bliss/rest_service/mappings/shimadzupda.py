# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.shimadzupda import ShimadzuPdaType

logger = logging.getLogger(__name__)


class ShimadzuPda(ObjectMapping):
    TYPE = ShimadzuPdaType

    CALLABLE_MAP = {
        "connect_pda": "connect_pda",
        "disconnect_pda": "disconnect_pda",
        "read_wl": "read_wl",
        "read_all": "read_all",
        "start_read": "start_read",
        "stop_read": "stop_read",
    }

    PROPERTY_MAP = {
        "data": HardwareProperty("data"),
    }


Default = ShimadzuPda
