# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import logging
import numbers

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

from lima2.client.pipelines.smx import Smx

_logger = logging.getLogger("bliss.ctrl.lima2.processing")


class Processing(Settings, HasRoi):
    """Classic processing user interface"""

    def __init__(self, config):
        self._device = None
        self._params = Smx.params_default
        super().__init__(config, path=["smx"])

        class Fai(Settings):
            """
            {
                "absorption_path": "",
                "acc_nb_frames_reset": 10,
                "acc_nb_frames_xfer": 10,
                "cl_source_path": "/opt/lima2/processings/common/fai/kernels",
                "csr_path": "/opt/detector/calibration/csr.h5",
                "cutoff_clip": 0.0,
                "cutoff_pick": 4.0,
                "cycle": 3,
                "dark_path": "",
                "dark_variance_path": "",
                "delta_dummy": 0.0,
                "dummy": 0.0,
                "error_model": "poisson",
                "flat_path": "",
                "mask_path": "/opt/detector/calibration/mask.h5",
                "noise": 0.6,
                "normalization_factor": 1.0,
                "polarization_path": "",
                "radius1d_path": "/opt/detector/calibration/bin_centers.h5",
                "radius2d_path": "/opt/detector/calibration/r_center.h5",
                "solid_angle_path": "",
                "variance_path": "",
            }

            """

            def __init__(self, config, fai_params):
                self._params = fai_params
                super().__init__(config, path=["smx", "fai"])

            @setting_property(default="")
            def absorption_path(self):
                return self._params["absorption_path"]

            @absorption_path.setter
            @typecheck
            def absorption_path(self, value: str):
                self._params["absorption_path"] = value

            @setting_property(default=10)
            def acc_nb_frames_reset(self):
                return self._params["acc_nb_frames_reset"]

            @acc_nb_frames_reset.setter
            @typecheck
            def acc_nb_frames_reset(self, value: int):
                self._params["acc_nb_frames_reset"] = value

            @setting_property(default=10)
            def acc_nb_frames_xfer(self):
                return self._params["acc_nb_frames_xfer"]

            @acc_nb_frames_xfer.setter
            @typecheck
            def acc_nb_frames_xfer(self, value: int):
                self._params["acc_nb_frames_xfer"] = value

            @setting_property(default="/opt/lima2/processings/common/fai/kernels")
            def cl_source_path(self):
                return self._params["cl_source_path"]

            @cl_source_path.setter
            @typecheck
            def cl_source_path(self, value: str):
                self._params["cl_source_path"] = value

            @setting_property(default="/opt/detector/calibration/csr.h5")
            def csr_path(self):
                return self._params["csr_path"]

            @csr_path.setter
            @typecheck
            def csr_path(self, value: str):
                self._params["csr_path"] = value

            @setting_property(default=0.0)
            def cutoff_clip(self):
                return self._params["cutoff_clip"]

            @cutoff_clip.setter
            @typecheck
            def cutoff_clip(self, value: numbers.Real):
                self._params["cutoff_clip"] = value

            @setting_property(default=4.0)
            def cutoff_pick(self):
                return self._params["cutoff_pick"]

            @cutoff_pick.setter
            @typecheck
            def cutoff_pick(self, value: numbers.Real):
                self._params["cutoff_pick"] = value

            @setting_property(default=3)
            def cycle(self):
                return self._params["cycle"]

            @cycle.setter
            @typecheck
            def cycle(self, value: int):
                self._params["cycle"] = value

            @setting_property(default="")
            def dark_path(self):
                return self._params["dark_path"]

            @dark_path.setter
            @typecheck
            def dark_path(self, value: str):
                self._params["dark_path"] = value

            @setting_property(default="")
            def dark_variance_path(self):
                return self._params["dark_variance_path"]

            @dark_variance_path.setter
            @typecheck
            def dark_variance_path(self, value: str):
                self._params["dark_variance_path"] = value

            @setting_property(default=0.0)
            def delta_dummy(self):
                return self._params["delta_dummy"]

            @delta_dummy.setter
            @typecheck
            def delta_dummy(self, value: numbers.Real):
                self._params["delta_dummy"] = value

            @setting_property(default=0.0)
            def dummy(self):
                return self._params["dummy"]

            @dummy.setter
            @typecheck
            def dummy(self, value: numbers.Real):
                self._params["dummy"] = value

            @setting_property(default="poisson")
            def error_model(self):
                return self._params["error_model"]

            @error_model.setter
            @typecheck
            def error_model(self, value: str):
                assert value in ["no_var", "variance", "poisson", "azimuthal", "hybrid"]
                self._params["error_model"] = value

            @setting_property(default="")
            def flat_path(self):
                return self._params["flat_path"]

            @flat_path.setter
            @typecheck
            def flat_path(self, value: str):
                self._params["flat_path"] = value

            @setting_property(default="")
            def mask_path(self):
                return self._params["mask_path"]

            @mask_path.setter
            @typecheck
            def mask_path(self, value: str):
                self._params["mask_path"] = value

            @setting_property(default=0.6)
            def noise(self):
                return self._params["noise"]

            @noise.setter
            @typecheck
            def noise(self, value: numbers.Real):
                self._params["noise"] = value

            @setting_property(default=1.0)
            def normalization_factor(self):
                return self._params["normalization_factor"]

            @normalization_factor.setter
            @typecheck
            def normalization_factor(self, value: numbers.Real):
                self._params["normalization_factor"] = value

            @setting_property(default="")
            def polarization_path(self):
                return self._params["polarization_path"]

            @polarization_path.setter
            @typecheck
            def polarization_path(self, value: str):
                self._params["polarization_path"] = value

            @setting_property(default="/opt/detector/calibration/bin_centers.h5")
            def radius1d_path(self):
                return self._params["radius1d_path"]

            @radius1d_path.setter
            @typecheck
            def radius1d_path(self, value: str):
                self._params["radius1d_path"] = value

            @setting_property(default="/opt/detector/calibration/r_center.h5")
            def radius2d_path(self):
                return self._params["radius2d_path"]

            @radius2d_path.setter
            @typecheck
            def radius2d_path(self, value: str):
                self._params["radius2d_path"] = value

            @setting_property(default="")
            def solid_angle_path(self):
                return self._params["solid_angle_path"]

            @solid_angle_path.setter
            @typecheck
            def solid_angle_path(self, value: str):
                self._params["solid_angle_path"] = value

            @setting_property(default="")
            def variance_path(self):
                return self._params["variance_path"]

            @variance_path.setter
            @typecheck
            def variance_path(self, value: str):
                self._params["variance_path"] = value

            def __info__(self):
                return "Fai:\n" + tabulate(self._params) + "\n\n"

        self.fai = Fai(config, self._params["fai"])

        self.saving_dense = Saving(
            config, ["smx", "saving_dense"], self._params["saving_dense"], "Dense"
        )
        self.saving_sparse = Saving(
            config, ["smx", "saving_sparse"], self._params["saving_sparse"], "Sparse"
        )
        self.saving_accumulation_corrected = Saving(
            config,
            ["smx", "saving_accumulation_corrected"],
            self._params["saving_accumulation_corrected"],
            "Accumulation Corrected",
        )
        self.saving_accumulation_peak = Saving(
            config,
            ["smx", "saving_accumulation_peak"],
            self._params["saving_accumulation_peak"],
            "Accumulation Peak",
        )

        self.roi_stats = RoiStatistics(
            config, ["smx", "statistics"], self._params["statistics"]
        )
        self.roi_profiles = RoiProfiles(
            config, ["smx", "profiles"], self._params["profiles"]
        )

    def _init_with_device(self, device):
        self._device = device

        # Define counters (but only once the device has been properly initialized)
        self._dense_frame_cnt = FrameCounter(
            "frame", device._frame_cc, "procs.saving_dense"
        )
        self._sparse_frame_cnt = FrameCounter(
            "sparse_frame", device._frame_cc, "procs.saving_sparse"
        )
        self._acc_corrected_frame_cnt = FrameCounter(
            "acc_corrected", device._frame_cc, "procs.saving_accumulation_corrected"
        )
        self._acc_peaks_frame_cnt = FrameCounter(
            "acc_peaks", device._frame_cc, "procs.saving_accumulation_peak"
        )

        super()._init_with_device(device)

    @property
    def nb_fifo_frames(self):
        return self._params["fifo"]["nb_fifo_frames"]

    @nb_fifo_frames.setter
    def nb_fifo_frames(self, value: int):
        self._params["fifo"]["nb_fifo_frames"] = value

    @setting_property(default=100)
    def nb_frames_buffer(self):
        return self._params["buffers"]["nb_frames_buffer"]

    @nb_frames_buffer.setter
    @typecheck
    def nb_frames_buffer(self, value: int):
        self._params["buffers"]["nb_frames_buffer"] = value

    @setting_property(default=100)
    def nb_peak_counters_buffer(self):
        return self._params["buffers"]["nb_peak_counters_buffer"]

    @nb_peak_counters_buffer.setter
    @typecheck
    def nb_peak_counters_buffer(self, value: int):
        self._params["buffers"]["nb_peak_counters_buffer"] = value

    @setting_property(default=100)
    def nb_roi_statistics_buffer(self):
        return self._params["buffers"]["nb_roi_statistics_buffer"]

    @nb_roi_statistics_buffer.setter
    @typecheck
    def nb_roi_statistics_buffer(self, value: int):
        self._params["buffers"]["nb_roi_statistics_buffer"] = value

    @setting_property(default=100)
    def nb_roi_profiles_buffer(self):
        return self._params["buffers"]["nb_roi_profiles_buffer"]

    @nb_roi_profiles_buffer.setter
    @typecheck
    def nb_roi_profiles_buffer(self, value: int):
        self._params["buffers"]["nb_roi_profiles_buffer"] = value

    @setting_property(default=0)
    def gpu_platform_idx(self):
        return self._params["gpu"]["platform_idx"]

    @gpu_platform_idx.setter
    @typecheck
    def gpu_platform_idx(self, value: int):
        self._params["gpu"]["platform_idx"] = value

    @setting_property(default=0)
    def gpu_device_idx(self):
        return self._params["gpu"]["device_idx"]

    @gpu_device_idx.setter
    @typecheck
    def gpu_device_idx(self, value: int):
        self._params["gpu"]["device_idx"] = value

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
            return f"{title}:\n" + tabulate(params) + "\n\n"

        smx_params = {k: getattr(self, k) for k in ["nb_fifo_frames"]}

        return "\n".join(
            [
                format("SMX", smx_params),
                format("Buffers", self._params["buffers"]),
                format("GPU", self._params["gpu"]),
                self.roi_stats.__info__(),
                self.roi_profiles.__info__(),
                self.fai.__info__(),
                self.saving_dense.__info__(),
                self.saving_sparse.__info__(),
                self.saving_accumulation_corrected.__info__(),
                self.saving_accumulation_peak.__info__(),
            ]
        )

    @property
    def counters(self):
        return [
            self._dense_frame_cnt,
            self._sparse_frame_cnt,
            self._acc_corrected_frame_cnt,
            self._acc_peaks_frame_cnt,
            *self._get_roi_counters(),
        ]

    @property
    def counter_groups(self):
        return {
            "images": counter_namespace(
                [
                    self._dense_frame_cnt,
                    self._sparse_frame_cnt,
                    self._acc_corrected_frame_cnt,
                    self._acc_peaks_frame_cnt,
                ]
            ),
            "rois": counter_namespace(self._get_roi_counters()),
        }
