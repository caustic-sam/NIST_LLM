#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    HardwareProperty,
)
from ..types.test import TestType, TestStates

logger = logging.getLogger(__name__)


class StateProperty(HardwareProperty):
    def translate_from(self, value):
        if value == 1:
            return TestStates[0]

        return ["UNKNOWN"]


class Test(ObjectMapping):
    TYPE = TestType

    PROPERTY_MAP = {
        "number": HardwareProperty("number"),
        "number_positive": HardwareProperty("number_positive"),
        "string": HardwareProperty("string"),
        "option": HardwareProperty("option"),
        "state": StateProperty("state"),
        "read_only": HardwareProperty("read_only"),
    }

    def _call_func0(self):
        self._object.func0()

    def _call_func1(self, value: str):
        self._object.func1(value)

    def _call_func_mul(self, a: float, b: float) -> float:
        return self._object.func_mul(a, b)

    def _call_long_process(self):
        return self._object.long_process()


Default = Test
