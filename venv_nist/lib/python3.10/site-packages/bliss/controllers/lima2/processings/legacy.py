# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import logging

from bliss.common.protocols import counter_namespace
from bliss.common.utils import typecheck

from bliss.controllers.lima2.counter import FrameCounter
from bliss.controllers.lima2.settings import Settings, setting_property
from bliss.controllers.lima2.tabulate import tabulate

from bliss.controllers.lima2.processings.common import (
    Saving,
    RoiStatistics,
    RoiProfiles,
    HasRoi,
)

from lima2.client.pipelines.legacy import Legacy

_logger = logging.getLogger("bliss.ctrl.lima2.processing")


class Processing(Settings, HasRoi):
    """Classic processing user interface"""

    def __init__(self, config):
        self._device = None
        self._params = Legacy.params_default
        super().__init__(config, path=["classic"])

        self.saving = Saving(config, ["classic", "saving"], self._params["saving"])
        self.roi_stats = RoiStatistics(
            config, ["classic", "statistics"], self._params["statistics"]
        )
        self.roi_profiles = RoiProfiles(
            config, ["classic", "profiles"], self._params["profiles"]
        )

    def _init_with_device(self, device):
        self._device = device

        # Define counters (but only once the device has been properly initialized)
        self._frame_cnt = FrameCounter("frame", device._frame_cc, "procs.saving")
        self._input_frame_cnt = FrameCounter("input_frame", device._frame_cc, None)

        super()._init_with_device(device)

    @property
    def nb_fifo_frames(self):
        return self._params["fifo"]["nb_fifo_frames"]

    @nb_fifo_frames.setter
    def nb_fifo_frames(self, value: int):
        self._params["fifo"]["nb_fifo_frames"] = value

    @setting_property(default=False)
    def use_mask(self):
        return self._params["mask"]["enabled"]

    @use_mask.setter
    @typecheck
    def use_mask(self, value: bool):
        self._params["mask"]["enabled"] = value

    @setting_property
    def mask(self):
        return self._params["mask"]["path"]

    @mask.setter
    @typecheck
    def mask(self, value: str):
        self._params["mask"]["path"] = value

    @setting_property(default=False)
    def use_flatfield(self):
        return self._params["flatfield"]["enabled"]

    @use_flatfield.setter
    @typecheck
    def use_flatfield(self, value: bool):
        self._params["flatfield"]["enabled"] = value

    @setting_property
    def flatfield(self):
        return self._params["flatfield"]["path"]

    @flatfield.setter
    @typecheck
    def flatfield(self, value: str):
        self._params["flatfield"]["path"] = value

    @setting_property(default=False)
    def use_background(self):
        return self._params["background"]["enabled"]

    @use_background.setter
    @typecheck
    def use_background(self, value: bool):
        self._params["background"]["enabled"] = value

    @setting_property
    def background(self):
        return self._params["background"]["path"]

    @background.setter
    @typecheck
    def background(self, value: str):
        self._params["background"]["path"] = value

    @setting_property(default=False)
    def use_roi_stats(self):
        return self._params["statistics"]["enabled"]

    @use_roi_stats.setter
    @typecheck
    def use_roi_stats(self, value: bool):
        self._params["statistics"]["enabled"] = value

    @setting_property(default=False)
    def use_roi_profiles(self):
        return self._params["profiles"]["enabled"]

    @use_roi_profiles.setter
    @typecheck
    def use_roi_profiles(self, value: bool):
        self._params["profiles"]["enabled"] = value

    def __info__(self):
        def format(title, params):
            return f"{title}:\n" + tabulate(params) + "\n"

        return "\n".join(
            [
                format("Accumulation", self._params["accumulation"]),
                format("Mask", self._params["mask"]),
                format("Flatfield", self._params["flatfield"]),
                format("Background", self._params["background"]),
                self.roi_stats.__info__(),
                self.roi_profiles.__info__(),
                self.saving.__info__(),
            ]
        )

    @property
    def counters(self):
        return [self._input_frame_cnt, self._frame_cnt, *self._get_roi_counters()]

    @property
    def counter_groups(self):
        return {
            # "images": counter_namespace([self._frame_cnt]),
            "images": counter_namespace([self._input_frame_cnt, self._frame_cnt]),
            "rois": counter_namespace(self._get_roi_counters()),
        }
