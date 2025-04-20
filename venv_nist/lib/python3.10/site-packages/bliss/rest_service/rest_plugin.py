# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import os
import types
import importlib
import pkgutil
import logging
from typing import Any

from .typedef import ObjectType
from .mappingdef import ObjectMapping


_logger = logging.getLogger(__name__)


class RestPlugin:
    """
    Base class for REST plugins.

    It contains helpers, and dummy functions
    which can be re-implemented.
    """

    #
    # To be re-implemented
    #

    def get_object_type_names(self) -> list[str]:
        """
        Re-implement to return the list of exposed object types
        """
        raise NotImplementedError()

    def get_object_type(self, obj_type: str) -> type[ObjectType]:
        """
        Re-implement to return the object type.

        Arguments:
            obj_type: Name of an object type
        """
        raise NotImplementedError()

    def get_mapping_class_from_obj(self, obj: Any) -> type[ObjectMapping] | None:
        """
        Re-implement to return the mapped object from a BLISS object.

        Arguments:
            obj_type: Name of an object type
        """
        raise NotImplementedError()

    #
    # Helpers
    #

    def get_module_names_from_package(self, package: types.ModuleType) -> list[str]:
        """
        Helper for the plugins to return the list of object types.
        """
        location = os.path.dirname(package.__file__ or "")
        assert location is not None
        names = [p.name for p in pkgutil.walk_packages([location])]
        return names

    def get_object_type_from_package(
        self, obj_type: str, package: types.ModuleType
    ) -> type[ObjectType]:
        """
        Helper for the plugins to return an object type.

        Arguments:
            obj_type: Name of an object type
            package: Root pachage containing a module `obj_type` which contain
                     the object type definition
        """
        try:
            mod = importlib.import_module(f".{obj_type}", package.__name__)
            # mod = importlib.reload(mod)
        except ImportError:
            raise
        class_name = obj_type.title()
        cls = getattr(mod, "Default", None)
        if cls is None:
            raise TypeError(
                f"{class_name} from {package.__name__}.{obj_type} does not exist"
            )
        if not isinstance(cls, type):
            raise TypeError(
                f"{class_name} from {package.__name__}.{obj_type} is not a ObjectType"
            )
        if not issubclass(cls, ObjectType):
            raise TypeError(
                f"{class_name} from {package.__name__}.{obj_type} is not a ObjectType"
            )
        return cls

    def get_mapping_class_from_package(
        self, mapping_name: str, package: types.ModuleType
    ) -> type[ObjectMapping]:
        """
        Helper for the plugins to return a mapped type by name.

        Arguments:
            mapping_name: Name of the object mapping
            package: Root pachage containing a module `mapping_name` which contain
                     the mapped object class.
        """
        # Making sure that everything up to the last module
        # is treated as base module
        base = package.__name__
        module = mapping_name
        if "." in module:
            mod_parts = module.split(".")
            base = base + "." + ".".join(mod_parts[0:-1])
            module = mod_parts[-1]
        # Load class from module
        mod_file = base + "." + module
        try:
            mod = importlib.import_module(mod_file)
            mod = importlib.reload(mod)
        except ModuleNotFoundError:
            _logger.error("Couldn't find module %s", mod_file)
            raise

        # Import class
        cls = getattr(mod, "Default", None)

        if not isinstance(cls, type) or not issubclass(cls, ObjectMapping):
            _logger.error("Couldn't import '%s' from %s", "Default", mod_file)
            raise TypeError(f"Couldn't import 'Default' from {mod_file}")

        return cls
