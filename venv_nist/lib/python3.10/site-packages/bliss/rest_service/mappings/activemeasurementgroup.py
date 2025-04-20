#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from bliss import _get_current_session
from bliss.common import event
from bliss.common.measurementgroup import get_active_name, set_active_name
from bliss.rest_service.mappingdef import (
    ObjectMapping,
    AbstractHardwareProperty2,
)

from ..types.objectref import ObjectrefType


logger = logging.getLogger(__name__)


class _ActiveMeasurementGroupRefProperty(AbstractHardwareProperty2):
    def __init__(self, parent):
        AbstractHardwareProperty2.__init__(self, parent)
        self._session = None

    def connect_hardware(self, obj):
        session = _get_current_session()
        event.connect(session, "active_mg", self._active_mg_updated)
        self._session = session

    def disconnect(self, obj):
        if self._session is not None:
            event.disconnect(self._session, "active_mg", self._active_mg_updated)
            self._session = None

    def _active_mg_updated(self, value, *args, **kwargs):
        if value is None:
            name = "hardware:"
        else:
            name = f"hardware:{value}"
        self.emit_update(name)

    def read_hardware(self, obj):
        """Read the value from the subsystem"""
        name = get_active_name()
        if name is None:
            return "hardware:"
        return f"hardware:{name}"

    def write_hardware(self, obj, value: str):
        """Write the value to the subsystem"""
        if not value.startswith("hardware:"):
            logger.warning("Unsupported hardware ref '%s'", value)
            return
        name = value[9:]
        set_active_name(name)


class Activemeasurementgroup(ObjectMapping):
    TYPE = ObjectrefType

    def _create_properties(self):
        return {
            "ref": _ActiveMeasurementGroupRefProperty(self),
        }


Default = Activemeasurementgroup
