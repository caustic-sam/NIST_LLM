#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
    ObjectRefProperty,
)
from ..types.objectref import ObjectrefType

logger = logging.getLogger(__name__)


class Objectref(ObjectMapping):
    TYPE = ObjectrefType

    PROPERTY_MAP = {"ref": ObjectRefProperty("ref")}


Default = Objectref
