#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.common.procedures.base_procedure import ProcedureState
from bliss.common.procedures.base_procedure import ProcedureExecusionState
from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
    EnumProperty,
)
from ..types.procedure import ProcedureType

logger = logging.getLogger(__name__)


class Procedure(ObjectMapping):
    TYPE = ProcedureType

    PROPERTY_MAP = {
        "state": EnumProperty("state", enum_type=ProcedureState),
        "previous_run_state": EnumProperty(
            "previous_run_state", enum_type=ProcedureExecusionState
        ),
        "previous_run_exception": HardwareProperty("previous_run_exception"),
        "parameters": HardwareProperty("parameters"),
    }

    CALLABLE_MAP = {
        "start": "start",
        "abort": "abort",
        "clear": "clear",
        "validate": "validate",
    }


Default = Procedure
