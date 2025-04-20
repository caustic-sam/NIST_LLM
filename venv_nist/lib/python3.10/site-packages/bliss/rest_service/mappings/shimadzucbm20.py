# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.shimadzucbm20 import (
    ShimadzuCBM20Type,
)

logger = logging.getLogger(__name__)


class ShimadzucBm20(ObjectMapping):
    TYPE = ShimadzuCBM20Type

    CALLABLE_MAP = {
        "connect_cbm20": "connect_cbm20",
        "disconnect_cbm20": "disconnect_cbm20",
        "start_pump": "start_pump",
        "stop_pump": "stop_pump",
        "set_pump_max_pressure": "set_pump_max_pressure",
        "set_pump_min_pressure": "set_pump_min_pressure",
        "set_pump_flow": "set_pump_flow",
        "set_pump_flow_threshold": "set_pump_flow_threshold",
        "set_flow_mode": "set_flow_mode",
        "select_solenoid_valve": "set_auto_sampler_temp",
        "start_auto_purge": "start_auto_purge",
        "stop_auto_purge": "stop_auto_purge",
        "inject_from_vial": "inject_from_vial",
        "stop_inject": "stop_inject",
        "enable_auto_sampler": "enable_auto_sampler",
        "disable_auto_sampler": "disable_auto_sampler",
        "set_auto_sampler_temp": "set_auto_sampler_temp",
        "pump_from_port": "pump_from_port",
    }

    PROPERTY_MAP = {
        "state": HardwareProperty("state"),
        "data": HardwareProperty("data"),
    }


Default = ShimadzucBm20
