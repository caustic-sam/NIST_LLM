# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import logging
from typing import Any
from importlib.metadata import entry_points

from ..typedef import ObjectType
from ..mappingdef import ObjectMapping
from ..mappings.unknown import Unknown
from ..rest_plugin import RestPlugin


logger = logging.getLogger(__name__)


class ObjectFactory:
    """
    Factory which create objects for the BLISS REST API.

    It hold the definitions of every mapped object. And load the
    BLISS object from the configuration. It also feature the access
    to the object types.
    """

    def __init__(self) -> None:
        self._plugins: list[RestPlugin] = []
        eps = entry_points()
        for entry_point in eps.select(group="bliss_rest"):
            plugin_cls = entry_point.load()
            if entry_point.name == "bliss":
                self._plugins.insert(0, plugin_cls())
            else:
                self._plugins.append(plugin_cls())

        self._object_types: dict[str, type[ObjectType]] = {}
        for p in self._plugins:
            names = p.get_object_type_names()
            for name in names:
                if name in self._object_types:
                    continue
                try:
                    self._object_types[name] = p.get_object_type(name)
                except Exception:
                    logger.error(
                        "Impossible to load object type '%s' from plugin '%s'", name, p
                    )
                    logger.debug(
                        "Impossible to load object type '%s' from plugin '%s'",
                        name,
                        p,
                        exc_info=True,
                    )

    def disconnect(self):
        pass

    def create_mapping_object(self, bliss_obj: Any, name: str) -> ObjectMapping:
        """
        Create a mapped object from a BLISS object.

        Arguments:
            bliss_obj: A BLISS object, or few dummies from `bliss.rest_service.dummies`
            name: The name of this object
        Returns:
            A mapped object
        """
        mapped_class = self._get_mapping_class(bliss_obj)
        return mapped_class(obj=bliss_obj, name=name)

    def _get_mapping_class(self, bliss_obj: Any) -> type[ObjectMapping]:
        # Making sure that everything up to the last module
        # is treated as base module
        for p in reversed(self._plugins):
            cls = p.get_mapping_class_from_obj(bliss_obj)
            if cls is not None:
                return cls
        return Unknown

    def get_abstract_objects(self) -> list[type[ObjectType]]:
        """Return the list of available abstract objects"""
        return list(self._object_types.values())

    def get_abstract_object(self, obj_type: str) -> type[ObjectType] | None:
        return self._object_types.get(obj_type)
