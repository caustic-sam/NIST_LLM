# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import time
import numpy
from typing import Any
import gevent
import logging
from flask_socketio import SocketIO
from bliss.common import event
from .object_store import ObjectStore
from ..mappingdef import ObjectMapping

logger = logging.getLogger(__name__)


class ObjectEvents:
    """
    Component responsible of sending the whole hardware events through
    the hardware web socket.
    """

    def __init__(self, hardware_store: ObjectStore, socketio: SocketIO):
        self._hardware_store = hardware_store
        self._socketio = socketio
        self._last_value: dict[str, dict[str, Any]] = {}
        self._emit_timeout: dict[str, dict[str, gevent.Greenlet | None]] = {}
        self._last_emit_time: dict[str, dict[str, float]] = {}

        for obj in hardware_store.get_objects():
            obj.subscribe("all", self._obj_param_change)
            obj.subscribe_online(self._obj_online_change)
            obj.subscribe_locked(self._obj_locked_change)

        event.connect(
            self._hardware_store,
            "object_registered",
            self.__object_registered,
        )
        event.connect(
            self._hardware_store,
            "object_unregistered",
            self.__object_unregistered,
        )

    def disconnect(self):
        event.disconnect(
            self._hardware_store,
            "object_registered",
            self.__object_registered,
        )
        event.disconnect(
            self._hardware_store,
            "object_unregistered",
            self.__object_unregistered,
        )

        for prop_list in self._emit_timeout.values():
            for g in prop_list.values():
                if g is not None:
                    g.kill()
        self._emit_timeout = {}

    def _ensure_last_val(self, obj: str, prop: str):
        """Ensure a last value

        Args:
            obj (str): The object id
            prop (str): The property

        Ensure a last value for obj/prop
        """
        if obj not in self._last_value:
            self._last_value[obj] = {}
        if obj not in self._emit_timeout:
            self._emit_timeout[obj] = {}
        if obj not in self._last_emit_time:
            self._last_emit_time[obj] = {}

        if prop not in self._last_value[obj]:
            self._last_value[obj][prop] = None
        if prop not in self._emit_timeout[obj]:
            self._emit_timeout[obj][prop] = None
        if prop not in self._last_emit_time[obj]:
            self._last_emit_time[obj][prop] = 0

    def _obj_online_change(self, obj: ObjectMapping, value: Any):
        """Object online callback

        Emits socketio event on online change
        """
        self._ensure_last_val(obj.name, "online")

        if self._last_value[obj.name]["online"] != value:
            self.emit("online", {"name": obj.name, "id": obj.name, "state": value})
        else:
            logger.debug(f"Duplicate update for {obj.name}, online, {value}")

    def _obj_locked_change(self, obj: ObjectMapping, locked_info: dict | None):
        """Object locked callback

        Emits socketio event on lock change
        """
        self._ensure_last_val(obj.name, "_locked")
        cache = self._last_value[obj.name]
        if cache["_locked"] != locked_info:
            cache["_locked"] = locked_info
            self.emit(
                "locked", {"name": obj.name, "id": obj.name, "state": locked_info}
            )
        else:
            logger.debug(f"Duplicate update for {obj.name}, locked, {locked_info}")

    def _obj_param_change(self, obj: ObjectMapping, prop: str, value: Any):
        """Object parameter change callback

        Emits a socketio event on parameter change

        Args:
            obj (obj): The hardware object whos parameter changed
            prop (str): The property that changed
            value (mixed): The new value
        """
        self._ensure_last_val(obj.name, prop)

        if isinstance(value, numpy.ndarray):
            # the following compare with `_last_value` and the socketio event do not support numpy array
            if value.size > 10:
                # Warn in case you are about to transmit huge data by mistakes
                logging.warning(
                    "Prop '%s' are about to transmit a numpy array (size %s) using a socketio event, with a slow conversion",
                    prop,
                    value.size,
                )
            value = value.tolist()

        if self._last_value[obj.name][prop] != value:
            self._queue_emit_value(obj, prop, value)
            self._last_value[obj.name][prop] = value

        else:
            logger.debug(f"Duplicate update for {obj.name}, {prop}, {value}")

    def _queue_emit_value(self, obj: ObjectMapping, prop: str, value: Any):
        g_prop = self._emit_timeout[obj.name][prop]
        if g_prop is not None:
            g_prop.kill()
            self._emit_timeout[obj.name][prop] = None

        now = time.time()
        if now - self._last_emit_time[obj.name][prop] > 0.2:
            self._emit_value(obj, prop, value)
        else:
            self._emit_timeout[obj.name][prop] = gevent.spawn_later(
                0.2, self._emit_value, obj, prop, value
            )

    def _emit_value(self, obj: ObjectMapping, prop: str, value: Any):
        data = {}
        data[prop] = value
        self.emit("change", {"name": obj.name, "id": obj.name, "data": data})
        self._last_emit_time[obj.name][prop] = time.time()

    def emit(self, event: str, data) -> None:
        """Emit a hardware message with socketio event.

        If the emit fails, a log is emitted but the method do not raise any
        exception.
        """
        try:
            self._socketio.emit(event, data, namespace="/object")
        except Exception:
            logger.error("Error while emitting socketio", exc_info=True)

    def __object_registered(self, obj: ObjectMapping, *args, **kwargs):
        obj.subscribe("all", self._obj_param_change)
        obj.subscribe_online(self._obj_online_change)
        obj.subscribe_locked(self._obj_locked_change)

    def __object_unregistered(self, obj: ObjectMapping, *args, **kwargs):
        obj.unsubscribe("all", self._obj_param_change)
        obj.unsubscribe_online(self._obj_online_change)
        obj.unsubscribe_locked(self._obj_locked_change)
