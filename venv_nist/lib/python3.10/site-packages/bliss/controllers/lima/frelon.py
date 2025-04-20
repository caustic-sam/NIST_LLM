# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from .lima_base import CameraBase
from .properties import LimaProperty


class Camera(CameraBase):
    def __init__(self, name, lima_device, proxy):
        CameraBase.__init__(self, name, lima_device, proxy)
        self.name = name
        self._proxy = proxy
        self._lima_device = lima_device

    def to_dict(self, *args, **kwargs):
        kwargs["include_properties"] = (
            "camera_serial",
            "e2v_correction",
            "image_mode",
            "input_channel",
            "roi_mode",
            "roi_bin_offset",
            "spb2_config",
        )
        return super().to_dict(*args, **kwargs)

    def calibrate(self, expo_time):
        """
        This is a procedure and it may take time...
        return current readout time and the maximum framerate
        """
        proxy = self._lima_device.proxy
        proxy.saving_mode = "MANUAL"

        self._lima_device.prepareAcq()
        transfer_time = self._proxy.transfer_time
        readout_time = self._proxy.readout_time
        if self._proxy.image_mode == "FRAME TRANSFER":
            return transfer_time, 1 / readout_time
        else:
            return readout_time, 1 / (readout_time + transfer_time)

    @LimaProperty
    def image_mode(self):
        return self._proxy.image_mode

    @image_mode.setter
    def image_mode(self, value):
        self._proxy.image_mode = value
        self._lima_device.image.update_max_size()

    def __info__(self):
        info = f"image_mode = {self.image_mode}\n"
        info += super().__info__()
        return info

    def command(self, frelon_command):
        return self._proxy.execSerialCommand(frelon_command)

    def hardware_config(self):
        ans = self._proxy.execSerialCommand(">C")
        print(ans)

    def hardware_status(self):
        ans = self._proxy.execSerialCommand(">V")
        print(ans)

    def hardware_warning(self):
        ans = self._proxy.execSerialCommand(">STC")
        print(ans)
