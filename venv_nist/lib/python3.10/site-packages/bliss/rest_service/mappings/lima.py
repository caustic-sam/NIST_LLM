#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

import gevent
import tango
import logging

from bliss.controllers.lima.lima_base import Lima as BlissLima
from bliss.common import event
from bliss.rest_service.mappingdef import (
    ObjectMapping,
    AbstractHardwareProperty2,
)
from ..types.lima import LimaType

_logger = logging.getLogger(__name__)


class _RawImageRoiProperty(AbstractHardwareProperty2):
    def read_hardware(self, obj: BlissLima):
        return obj.image._image_params._roi

    def write_hardware(self, obj: BlissLima, value):
        raise NotImplementedError("Raw roi can't be edited")

    def connect_hardware(self, obj: BlissLima):
        event.connect(obj.image._image_params, "_roi", self._update)

    def disconnect(self, obj: BlissLima):
        event.disconnect(obj.image._image_params, "_roi", self._update)

    def _update(self, value, *args, **kwargs):
        self.emit_update(value)


class _RoiProperty(AbstractHardwareProperty2):
    def read_hardware(self, obj: BlissLima):
        roi = obj.image._image_params._cur_roi
        return roi

    def write_hardware(self, obj: BlissLima, value):
        obj.image.roi = value

    def connect_hardware(self, obj: BlissLima):
        event.connect(obj.image._image_params, "_cur_roi", self._update)

    def disconnect(self, obj: BlissLima):
        event.disconnect(obj.image._image_params, "_cur_roi", self._update)

    def _update(self, value, *args, **kwargs):
        self.emit_update(value)


class _SizeProperty(AbstractHardwareProperty2):
    def __init__(self, parent):
        AbstractHardwareProperty2.__init__(self, parent)
        self._online = False

    def read_hardware(self, obj: BlissLima):
        roi = obj.image._image_params._cur_roi
        return roi[2:]

    def write_hardware(self, obj: BlissLima, value):
        raise NotImplementedError("Size can't be edited")

    def connect_hardware(self, obj: BlissLima):
        event.connect(obj.image._image_params, "_cur_roi", self._update)
        # Event when the detector is connected
        # BLISS is not able to update the size and ROI alone at the very first use
        # this will force the update of the property in Redis
        self._object.subscribe("state", self._on_hardware_state_change)

    def disconnect(self, obj: BlissLima):
        event.disconnect(obj.image._image_params, "_cur_roi", self._update)
        self._object.unsubscribe("state", self._on_hardware_state_change)

    def _on_hardware_state_change(self, obj, prop, value):
        online = value != "OFFLINE"
        if self._online == online:
            return
        self._online = online
        if self._online:
            obj = self._object._object
            roi = obj.image._image_params._cur_roi
            if roi == [0, 0, 0, 0]:
                # If it's the default value, reset
                obj.image._update_cur_roi(update_dependencies=False)

    def _update(self, value, *args, **kwargs):
        value = value[2:]
        self.emit_update(value)


class _RotationProperty(AbstractHardwareProperty2):
    FROM_HARDWARE = {
        "NONE": 0,
        "90": 90,
        "180": 180,
        "270": 270,
    }
    TO_HARDWARE = {
        0: "NONE",
        90: "90",
        180: "180",
        270: "270",
    }

    def read_hardware(self, obj: BlissLima):
        r = obj.image._image_params.rotation
        return self.FROM_HARDWARE[r]

    def write_hardware(self, obj: BlissLima, value):
        v = self.TO_HARDWARE[value]
        obj.image.rotation = v

    def connect_hardware(self, obj: BlissLima):
        event.connect(obj.image._image_params, "rotation", self._update)

    def disconnect(self, obj: BlissLima):
        event.disconnect(obj.image._image_params, "rotation", self._update)

    def _update(self, value, *args, **kwargs):
        v = self.FROM_HARDWARE[value]
        self.emit_update(v)


class _BinningProperty(AbstractHardwareProperty2):
    def read_hardware(self, obj: BlissLima):
        return obj.image._image_params.binning

    def write_hardware(self, obj: BlissLima, value):
        obj.image.binning = value

    def connect_hardware(self, obj: BlissLima):
        event.connect(obj.image._image_params, "binning", self._update)

    def disconnect(self, obj: BlissLima):
        event.disconnect(obj.image._image_params, "binning", self._update)

    def _update(self, value, *args, **kwargs):
        self.emit_update(value)


class _FlipProperty(AbstractHardwareProperty2):
    def read_hardware(self, obj: BlissLima):
        return obj.image._image_params.flip

    def write_hardware(self, obj: BlissLima, value):
        obj.image.flip = value

    def connect_hardware(self, obj: BlissLima):
        event.connect(obj.image._image_params, "flip", self._update)

    def disconnect(self, obj: BlissLima):
        event.disconnect(obj.image._image_params, "flip", self._update)

    def _update(self, value, *args, **kwargs):
        self.emit_update(value)


class _AccMaxExpoTimeProperty(AbstractHardwareProperty2):
    def __init__(self, parent):
        AbstractHardwareProperty2.__init__(self, parent)
        self._connected = False

    def read_hardware(self, obj: BlissLima):
        if not self._connected:
            return None
        return obj.accumulation.max_expo_time

    def write_hardware(self, obj: BlissLima, value):
        obj.accumulation.max_expo_time = value

    def connect_hardware(self, obj: BlissLima):
        try:
            event.connect(obj.accumulation, "max_expo_time", self._update)
            self._connected = True
        except Exception:
            # NOTE: At the very first start, the `obj.accumulation` will raise an
            #       exception the detector is not there. The work around is to delay
            #       until the detector is online
            # Connect this property to the changes of the state property
            self._object.subscribe("state", self._on_hardware_state_change)

    def disconnect(self, obj: BlissLima):
        self._object.unsubscribe("state", self._on_hardware_state_change)
        if self._connected:
            event.disconnect(obj.accumulation, "max_expo_time", self._update)

    def _on_hardware_state_change(self, obj: Lima, prop, value):
        if self._connected:
            return

        online = value != "OFFLINE"
        if not online:
            return

        event.connect(self._object._object.accumulation, "max_expo_time", self._update)
        self._connected = True
        result = self.read_hardware(self._object._object)
        self.emit_update(result)

    def _update(self, value, *args, **kwargs):
        self.emit_update(value)


class _TangoStaticProperty(AbstractHardwareProperty2):
    """
    Information which does not change during the life cycle of the detector.

    It's constant values which can be fetched from the device.

    For now, this is updated at the time the daiquiri hardware "state"
    change it's value.
    """

    def __init__(self, parent):
        AbstractHardwareProperty2.__init__(self, parent)
        self._online = False

    def _read_camera_pixel_size(self, proxy: tango.DeviceProxy) -> tuple[float, float]:
        """Returns the camera pixel size (unbinned) in micrometer"""
        ps = proxy.camera_pixelsize

        # Lima returns pixel size in meter
        # Some cameras was returning returning it in micron (PCO, Andor, Andor3)
        camera_type = proxy.camera_type.lower()
        if camera_type in ["pco", "andor", "andor3"]:
            # Handle patched and non patched Lima cameras
            if ps[0] > 0.1:
                # Sounds like it's already in micron
                pass
            else:
                ps = ps[0] * 1e6, ps[1] * 1e6
        else:
            ps = ps[0] * 1e6, ps[1] * 1e6

        return ps

    def _read_image_max_size(self, proxy: tango.DeviceProxy):
        w, h = proxy.image_max_dim
        return int(w), int(h)

    def read_hardware(self, obj: BlissLima):
        proxy = obj._proxy
        if proxy is None:
            raise RuntimeError("The detector is not online")

        try:
            proxy.ping()
        except Exception:
            return None

        return {
            "lima_version": proxy.lima_version,
            "lima_type": proxy.lima_type,
            "camera_type": proxy.camera_type,
            "camera_model": proxy.camera_model,
            "camera_pixelsize": self._read_camera_pixel_size(proxy),
            "image_max_dim": self._read_image_max_size(proxy),
        }

    def write_hardware(self, obj: BlissLima, value):
        raise NotImplementedError("structural property is a read only value")

    def connect_hardware(self, obj: BlissLima):
        # Connect this property to the changes of the state property
        self._object.subscribe("state", self._on_hardware_state_change)

    def disconnect(self, obj: BlissLima):
        self._object.unsubscribe("state", self._on_hardware_state_change)

    def _on_hardware_state_change(self, obj, prop, value):
        online = value != "OFFLINE"
        if self._online == online:
            return
        self._online = online
        if self._online:
            result = self.read_hardware(self._object._object)
            self.emit_update(result)
        else:
            self.emit_update(None)


class _TangoStateProperty(AbstractHardwareProperty2):
    """
    State of Lima from the `acq_state` Tango attribute.

    For now this is polled every 5 secondes.

    FIXME: Replace the polling by even subscription
    """

    FROM_HARDWARE = {
        "Ready": "READY",
        "Fault": "FAULT",
        "Running": "ACQUIRING",
        "Configuration": "CONFIGURATION",
        "?": "UNKNOWN",  # Valid value which can be returned by the LimaCCDs
    }

    def __init__(self, parent):
        AbstractHardwareProperty2.__init__(self, parent)
        self._value: str = "OFFLINE"
        self._gpolling = None

    def _polling(self):
        while True:
            gevent.sleep(5)
            try:
                v = self.read_hardware(self._object._object)
                if self._value != v:
                    self._value = v
                    self.emit_update(v)
            except Exception:
                _logger.error("Error while fetching state", exc_info=True)

    def read_hardware(self, obj: BlissLima):
        proxy = obj._proxy
        try:
            proxy.ping()
        except Exception:
            return "OFFLINE"
        r = proxy.acq_status
        return self.FROM_HARDWARE.get(r, "UNKNOWN")

    def write_hardware(self, obj: BlissLima, value):
        raise NotImplementedError("state is a read only value")

    def connect_hardware(self, obj: BlissLima):
        self._gpolling = gevent.spawn(self._polling)

    def disconnect(self, obj: BlissLima):
        if self._gpolling is not None:
            self._gpolling.kill()
            self._gpolling = None


class Lima(ObjectMapping):
    TYPE = LimaType

    def _create_properties(self):
        return {
            "state": _TangoStateProperty(self),
            "static": _TangoStaticProperty(self),
            "rotation": _RotationProperty(self),
            "binning": _BinningProperty(self),
            "size": _SizeProperty(self),
            "raw_roi": _RawImageRoiProperty(self),
            "roi": _RoiProperty(self),
            "flip": _FlipProperty(self),
            "acc_max_expo_time": _AccMaxExpoTimeProperty(self),
        }


Default = Lima
