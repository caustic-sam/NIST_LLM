# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import logging
import gevent
from typing import Any

from bliss.common import event
from bliss.config.conductor import client
from bliss.config.static import get_config, ObjectNotFound

from ..mappingdef import ObjectMapping
from .object_factory import ObjectFactory
from .. import dummies


logger = logging.getLogger(__name__)


class ObjectStore:
    """
    Store and create a set of monitored object.
    """

    def __init__(self, object_factory: ObjectFactory):
        self._object_factory = object_factory
        self._objects: dict[str, ObjectMapping] = {}
        self._lock_monitor = gevent.spawn(self._monitor_locked_device_loop)

    def _load_bliss_object(self, object_name: str) -> Any:
        """Return a BLISS object from a name.

        The result is a BLISS object loaded from yaml files, except for few
        special objects.

        Arguments:
            object_name: The name of the BLISS object as defined in the
                         in the yaml configuration, plus few other
                         special names only available for the REST API.
        """
        if object_name == "ACTIVE_MG":
            return dummies.DummyActiveMg(object_name)
        elif object_name == "SCAN_SAVING":
            return dummies.DummyScanSaving(object_name)
        elif object_name == "ACTIVE_TOMOCONFIG":
            return dummies.DummyActiveTomo(object_name)

        bliss_config = get_config()
        try:
            obj = bliss_config.get(object_name)
        except ObjectNotFound:
            raise
        except Exception:
            return dummies.DummyNotLoaded(object_name)
        return obj

    def register_object(self, object_name: str, register_sub_objects=False):
        """
        Register an object to the store.

        If the object is already stored, this does nothing.

        Arguments:
            object_name: Name of the object to register.
            register_sub_objects: If true, sub objects from this object
                                  are also registred.

        Raises:
            ObjectNotFound: In the name was not found in the BLISS
                            configuration.
        """
        if self.get_object(object_name) is not None:
            # The object is already registred
            return

        bliss_obj = self._load_bliss_object(object_name)
        obj = self._object_factory.create_mapping_object(bliss_obj, object_name)
        obj.set_online(obj.check_online())
        self._objects[obj.name] = obj
        try:
            if register_sub_objects:
                sub_names = obj.get_subobject_names()
                for sub_name in sub_names:
                    self.register_object(sub_name)
        finally:
            event.send(self, "object_registered", obj)

    def unregister_object(self, object_string: str):
        obj = self._objects.pop(object_string)
        event.send(self, "object_unregistered", obj)
        obj.disconnect()

    def _monitor_locked_device_loop(self):
        """
        Monitoring loop to check the state of the locks in beacon.

        FIXME: Such component moved out as part of the rest_service instead.
        """
        try:
            connection = client.get_default_connection()

            all_locks: dict[str, str] = {}
            while True:
                gevent.sleep(1.0)

                new_locks = connection.who_locked()
                new_keys = set(new_locks.keys())
                previous_keys = set(all_locks.keys())
                for k in new_keys - previous_keys:
                    obj = self.get_object(k)
                    if obj is None:
                        # obj is not exposed by the API
                        continue
                    obj.set_locked(new_locks[k])
                for k in previous_keys - new_keys:
                    obj = self.get_object(k)
                    if obj is None:
                        # obj is not exposed by the API
                        continue
                    obj.set_locked(None)
                all_locks = new_locks
        except Exception:
            logger.error("Error while monitoring locks", exc_info=True)

    def disconnect(self):
        for obj in self._objects.values():
            obj.disconnect()
        self._objects = {}
        self._lock_monitor.kill()
        self._lock_monitor = None

    def get_objects(self, type: str | None = None) -> list[ObjectMapping]:
        """Get a list of hardware objects

        Kwargs:
            type (str): Filter the list of objects by a type (i.e. motor)

        Returns:
            A list of objects
        """
        if type is not None:
            return [obj for obj in self._objects.values() if obj.type == type]
        else:
            return [obj for obj in self._objects.values()]

    def get_object(self, object_name: str) -> ObjectMapping | None:
        """Get a specific object

        Args:
            object_name: The BLISS name of the object

        Returns
            The object
        """
        return self._objects.get(object_name)

    def __info__(self):
        if len(self._objects) == 0:
            return "No exposed objects"
        result = ["List of exposed objects"]
        for name in self._objects.keys():
            result.append(name)
        return "\n".join(result)
