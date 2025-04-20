# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from .lima_base import CameraBase


class Camera(CameraBase):
    def __init__(self, name, limadev, proxy):
        super().__init__(name, limadev, proxy)
        self.name = name
        self._device = limadev
        self._proxy = proxy

    def to_dict(self, *args, **kwargs):
        kwargs["include_properties"] = ("frame_mode",)
        return super().to_dict(*args, **kwargs)

    @property
    def synchro_mode(self):
        return "IMAGE"

    def acquireNewBackground(self, nb_frames=1):
        """
        Takes background frames and ask Rayonix detector to apply the correction.
        """
        print(f"Will apply a new background correction with {nb_frames} frames ...")
        self._proxy.acquireNewBackground([0, nb_frames])
