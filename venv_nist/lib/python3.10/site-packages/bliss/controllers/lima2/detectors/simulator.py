# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numbers

from bliss.common.utils import typecheck
from bliss.common.counter import SamplingCounter
from bliss.common.protocols import counter_namespace

from bliss.controllers.lima2.tabulate import tabulate
from bliss.controllers.lima2.controller import DetectorStatusController
from bliss.controllers.lima2.settings import Settings, setting_property


class Detector(Settings):
    """Simulator detector user interface"""

    def __init__(self, device):
        self._params = device._ctrl_params["det"]
        super().__init__(device._config, path=["simulator"])

        self._det_cc = DetectorStatusController(device)
        self._temperature_cnt = SamplingCounter(
            "temperature", self._det_cc, unit="degC"
        )
        self._humidity_cnt = SamplingCounter("humidity", self._det_cc, unit="%")

        class Generator(Settings):
            """
            {
                "type": "gauss",
                "gauss": {
                    "peaks": [{"x0": 1024.0, "y0": 1024.0, "fwhm": 128.0, "max": 100.0}],
                    "grow_factor": 0.0,
                },
                "diffraction": {
                    "x0": 1024.0,
                    "y0": 1024.0,
                    "source_pos_x": 5.0,
                    "source_pos_y": 5.0,
                    "source_speed_x": 0.0,
                    "source_speed_y": 0.0,
                },
                "pixel_type": "gray8",
            }

            """

            def __init__(self, device):
                self._params = device._ctrl_params["det"]["generator"]
                super().__init__(device._config, path=["simulator", "generator"])

            @setting_property(default="gauss")
            def type(self):
                return self._params["type"]

            @type.setter
            @typecheck
            def type(self, value: str):
                assert value in ["gauss", "diffraction"]
                self._params["type"] = value

            @setting_property(default="gray16")
            def pixel_type(self):
                return self._params["pixel_type"]

            @pixel_type.setter
            @typecheck
            def pixel_type(self, value: str):
                self._params["pixel_type"] = value

            @setting_property(default=1)
            def nb_channels(self):
                return self._params["nb_channels"]

            @nb_channels.setter
            @typecheck
            def nb_channels(self, value: int):
                self._params["nb_channels"] = value

            @setting_property(
                default=[{"x0": 1024.0, "y0": 1024.0, "fwhm": 128.0, "max": 100.0}]
            )
            def peaks(self):
                return self._params["gauss"]["peaks"]

            @peaks.setter
            @typecheck
            def peaks(self, value: list):
                self._params["gauss"]["peaks"] = value

            @setting_property(default=0.0)
            def grow_factor(self):
                return self._params["gauss"]["grow_factor"]

            @grow_factor.setter
            @typecheck
            def grow_factor(self, value: numbers.Real):
                self._params["gauss"]["grow_factor"] = value

            def __info__(self):
                return tabulate(self._params) + "\n\n"

        self.generator = Generator(device)

        class Loader(Settings):
            """
            {
                "base_path": "/tmp",
                "dataset_path": "/entry_0000/measurement/data",
                "file_type": "nexus",
                "filename_format": "{filename_prefix}_{filename_rank}_{file_number:05d}{filename_suffix}",
                "filename_prefix": "lima2",
                "filename_rank": 0,
                "filename_suffix": ".h5",
                "frame_slice": {"count": 0,
                                "start": 0,
                                "stride": 1},
                "nb_frames_per_file": 50,
                "start_number": 0,
            }

            """

            def __init__(self, device):
                self._params = device._ctrl_params["det"]["loader"]
                super().__init__(device._config, path=["simulator", "loader"])

            @setting_property(default="/tmp")
            def base_path(self):
                return self._params["base_path"]

            @base_path.setter
            @typecheck
            def base_path(self, value: str):
                self._params["base_path"] = value

            @setting_property(default="/entry_0000/measurement/data")
            def dataset_path(self):
                return self._params["dataset_path"]

            @dataset_path.setter
            @typecheck
            def dataset_path(self, value: str):
                self._params["dataset_path"] = value

            @setting_property(default="nexus")
            def file_type(self):
                return self._params["file_type"]

            @file_type.setter
            @typecheck
            def file_type(self, value: str):
                self._params["file_type"] = value

            @setting_property(
                default="{filename_prefix}_{filename_rank}_{file_number:05d}{filename_suffix}"
            )
            def filename_format(self):
                return self._params["filename_format"]

            @filename_format.setter
            @typecheck
            def filename_format(self, value: str):
                self._params["filename_format"] = value

            @setting_property(default="lima2")
            def filename_prefix(self):
                return self._params["filename_prefix"]

            @filename_prefix.setter
            @typecheck
            def filename_prefix(self, value: str):
                self._params["filename_prefix"] = value

            @setting_property(default=0)
            def filename_rank(self):
                return self._params["filename_rank"]

            @filename_rank.setter
            @typecheck
            def filename_rank(self, value: int):
                self._params["filename_rank"] = value

            @setting_property(default=".h5")
            def filename_suffix(self):
                return self._params["filename_suffix"]

            @filename_suffix.setter
            @typecheck
            def filename_suffix(self, value: str):
                self._params["filename_suffix"] = value

            @setting_property(default=0)
            def frame_slice_start(self):
                return self._params["frame_slice"]["start"]

            @frame_slice_start.setter
            @typecheck
            def frame_slice_start(self, value: int):
                self._params["frame_slice"]["start"] = value

            @setting_property(default=0)
            def frame_slice_count(self):
                return self._params["frame_slice"]["count"]

            @frame_slice_count.setter
            @typecheck
            def frame_slice_count(self, value: int):
                self._params["frame_slice"]["count"] = value

            @setting_property(default=1)
            def frame_slice_stride(self):
                return self._params["frame_slice"]["stride"]

            @frame_slice_stride.setter
            @typecheck
            def frame_slice_stride(self, value: int):
                self._params["frame_slice"]["stride"] = value

            @setting_property(default=50)
            def nb_frames_per_file(self):
                return self._params["nb_frames_per_file"]

            @nb_frames_per_file.setter
            @typecheck
            def nb_frames_per_file(self, value: int):
                self._params["nb_frames_per_file"] = value

            def __info__(self):
                return tabulate(self._params) + "\n\n"

        self.loader = Loader(device)

    @setting_property(default="generator")
    def source(self):
        return self._params["image_source"]

    @source.setter
    @typecheck
    def source(self, value: str):
        assert value in ["generator", "loader"]
        self._params["image_source"] = value

    @setting_property(default=1)
    def nb_prefetch_frames(self):
        return self._params["nb_prefetch_frames"]

    @nb_prefetch_frames.setter
    @typecheck
    def nb_prefetch_frames(self, value: numbers.Integral):
        self._params["nb_prefetch_frames"] = value

    def __info__(self):
        return (
            f"{self.source.title()}:\n"
            + f"nb_prefetch_frames: {self.nb_prefetch_frames}\n\n"
            + getattr(self, self.source).__info__()
        )

    @property
    def counters(self):
        return [
            self._temperature_cnt,
            self._humidity_cnt,
        ]

    @property
    def counter_groups(self):
        res = {}
        res["health"] = counter_namespace([self._temperature_cnt, self._humidity_cnt])
        return res
