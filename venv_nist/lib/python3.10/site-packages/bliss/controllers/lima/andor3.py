# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from .properties import LimaProperty
from .lima_base import CameraBase


class Camera(CameraBase):
    def __init__(self, name, limadev, proxy):
        super().__init__(name, limadev, proxy)
        self.name = name
        self._device = limadev
        self._proxy = proxy

    def to_dict(self, *args, **kwargs):
        kwargs["include_properties"] = (
            "adc_gain",
            "adc_rate",
            "cooler",
            "electronic_shutter_mode",
            "overlap",
            "temperature",
            "temperature_sp",
            "serial_number",
        )
        return super().to_dict(*args, **kwargs)

    @property
    def synchro_mode(self):
        return "IMAGE"

    @LimaProperty
    def overlap(self):
        return self._proxy.overlap

    @overlap.setter
    def overlap(self, value):
        if value == "ON":
            if self._device._proxy.acq_trigger_mode not in (
                "INTERNAL_TRIGGER",
                "EXTERNAL_TRIGGER",
            ):
                self._device._proxy.acq_trigger_mode = "INTERNAL_TRIGGER"
                self._device._proxy.prepareAcq()
        self._proxy.overlap = value
