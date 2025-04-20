# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from .properties import LimaProperty
from .lima_base import CameraBase


class Camera(CameraBase):
    def __init__(self, lima_device, name, proxy):
        CameraBase.__init__(self, lima_device, name, proxy)
        self.name = name
        self._proxy = proxy

    def to_dict(self, *args, **kwargs):
        kwargs["include_properties"] = ("correction_mode", "gain")
        return super().to_dict(*args, **kwargs)

    @LimaProperty
    def keep_first_image(self):
        bool_str = self._proxy.keep_first_image
        return bool_str == "YES"

    @keep_first_image.setter
    def keep_first_image(self, value):
        bool_str = "YES" if value else "NO"
        self._proxy.keep_first_image = bool_str

    def start_acq_gain_image(self, nb_frames, exposure_time):
        self._proxy.startAcqGainImage(nb_frames, exposure_time)

    def start_acq_offset_image(self, nb_frames, exposure_time):
        self._proxy.startAcqOffsetImage(nb_frames, exposure_time)

    @property
    def synchro_mode(self):
        return "IMAGE"
