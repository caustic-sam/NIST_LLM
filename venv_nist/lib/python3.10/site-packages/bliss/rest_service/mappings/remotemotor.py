#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.remotemotor import (
    RemotemotorType,
    RemoteMotorStates,
)

logger = logging.getLogger(__name__)


class Remotemotor(ObjectMapping):
    TYPE = RemotemotorType

    def _get_state(self):
        return RemoteMotorStates[0]

    PROPERTY_MAP = {
        "resolution": HardwareProperty("resolution"),
        "state": HardwareProperty("state", getter=_get_state),
    }

    CALLABLE_MAP = {"enable": "enable", "disable": "disable"}


Default = Remotemotor
