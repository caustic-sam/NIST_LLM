# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import importlib
import logging
from typing import Any

from .rest_plugin import RestPlugin
from .typedef import ObjectType
from .mappingdef import ObjectMapping


_logger = logging.getLogger(__name__)


BLISS_CLASSNAMES = {
    "bliss.common.axis.axis.Axis": "motor",
    "bliss.controllers.actuator.Actuator": "actuator",
    "bliss.controllers.multiplepositions.MultiplePositions": "multiposition",
    "bliss.common.shutter.BaseShutter": "shutter",
    "bliss.controllers.intraled.Intraled": "intraled",
    "bliss.controllers.test.objectref.ObjectRef": "objectref",
    "bliss.controllers.lima.lima_base.Lima": "lima",
    "bliss.common.measurementgroup.MeasurementGroup": "measurementgroup",
    "bliss.common.procedures.base_procedure.SessionProcedure": "procedure",
    "bliss.testutils.rest_test_object.RestTestObject": "test",
    # Special objects
    "bliss.rest_service.dummies.DummyScanSaving": "scansaving",
    "bliss.rest_service.dummies.DummyActiveMg": "activemeasurementgroup",
}
"""
Mapping from `bliss` fully qualified class name to mapped object name located in
`bliss.rest_service.hardware.mapped`.
"""

SHORT_CLASSNAMES = {
    "EBV": "beamviewer",
    "volpi": "volpi",
    "Fshutter": "shutter",
    "transmission": "transmission",
    "tango_attr_as_counter": "tango_attr_as_counter",
    "ShimadzuCBM20": "shimadzucbm20",
    "ShimadzuPDA": "shimadzupda",
    "ID26Attenuator": "attenuator_wago",
    "PresetManager": "presetmanager",
    "LaserController": "laser",
    "LaserHeating": "laserheating",
    "IcePapTrack": "remotemotor",
    "DaiquiriProcessor": "processor",
}
"""
Mapping based on classname only.

This have to be removed and replaced by a fully qualified class name at some point.
"""


class DefaultRestPlugin(RestPlugin):
    def __init__(self) -> None:
        RestPlugin.__init__(self)
        self._class_map: dict[str, str] = {}
        self._class_map.update(BLISS_CLASSNAMES)

    def get_object_type_names(self) -> list[str]:
        from bliss.rest_service import types

        names = self.get_module_names_from_package(types)
        return names

    def get_object_type(self, obj_type: str) -> type[ObjectType]:
        from bliss.rest_service import types

        return self.get_object_type_from_package(obj_type, types)

    def get_mapping_class_from_obj(self, obj: Any) -> type[ObjectMapping] | None:
        from bliss.rest_service import mappings

        mapped_name = self.get_mapping_name_from_object(obj)
        if mapped_name is None:
            return None
        return self.get_mapping_class_from_package(mapped_name, mappings)

    def get_mapping_name_from_object(self, obj: Any) -> str | None:
        """
        Get a mapped class from an object.
        """
        if isinstance(obj, str):
            # Short cut for special types like
            # activemeasurementgroup, scansaving, activetomoconfig
            return obj

        for bliss_mapping, mapped_class_name in self._class_map.items():
            bliss_file, bliss_class_name = bliss_mapping.rsplit(".", 1)
            # Some classes may not be available depending on the bliss version
            try:
                bliss_module = importlib.import_module(bliss_file)
                bliss_class = getattr(bliss_module, bliss_class_name)
            except ModuleNotFoundError:
                _logger.warning("Could not find bliss module %s", bliss_mapping)
                continue
            if isinstance(obj, bliss_class):
                return mapped_class_name

        cls = obj.__class__.__name__
        if cls in SHORT_CLASSNAMES:
            return SHORT_CLASSNAMES[cls]

        return None
