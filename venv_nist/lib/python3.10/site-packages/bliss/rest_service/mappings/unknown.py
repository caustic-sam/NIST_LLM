#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

from bliss.rest_service.mappingdef import (
    ObjectMapping,
)
from ..types.unknown import UnknownType
from ..dummies import DummyNotLoaded

logger = logging.getLogger(__name__)


class Unknown(ObjectMapping):
    TYPE = UnknownType

    def check_online(self) -> bool:
        """Programatic check if the object is online"""
        return not isinstance(self._object, DummyNotLoaded)


Default = Unknown
