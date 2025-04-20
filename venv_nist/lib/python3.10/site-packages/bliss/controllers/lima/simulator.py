# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from .properties import LimaProperty
from .lima_base import CameraBase


class Camera(CameraBase):
    def __init__(self, name, lima_device, proxy):
        super().__init__(name, lima_device, proxy)
        self.__synchro_mode = "IMAGE"

    @LimaProperty
    def test(self):
        return "test"

    @property
    def test2(self):
        return "test2"

    @property
    def synchro_mode(self):
        return self.__synchro_mode

    @synchro_mode.setter
    def synchro_mode(self, value):
        if value in ["IMAGE", "TRIGGER"]:
            self.__synchro_mode = value
