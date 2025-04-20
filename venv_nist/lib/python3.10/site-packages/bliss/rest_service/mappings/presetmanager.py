#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.presetmanager import (
    PresetmanagerType,
    PresetmanagerStates,
)

logger = logging.getLogger(__name__)


class Presetmanager(ObjectMapping):
    TYPE = PresetmanagerType

    def _get_state(self):
        return PresetmanagerStates[0]

    PROPERTY_MAP = {
        "presets": HardwareProperty("presets"),
        "state": HardwareProperty("state", getter=_get_state),
    }

    CALLABLE_MAP = {"apply": "apply"}


Default = Presetmanager
