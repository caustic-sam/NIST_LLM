# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.common.utils import typecheck
from bliss.common.counter import SamplingCounter
from bliss.common.protocols import counter_namespace

from bliss.controllers.lima2.tabulate import tabulate
from bliss.controllers.lima2.controller import DetectorStatusController
from bliss.controllers.lima2.settings import Settings, setting_property


class Detector(Settings):
    """Smartpix detector user interface"""

    def __init__(self, device):
        self._params = device._ctrl_params["det"]
        super().__init__(device._config, path=["smartpix"])

        self._det = device._det
        self._det_cc = DetectorStatusController(device)

        self._temperature_cnt = SamplingCounter(
            "temperature", self._det_cc, unit="degC"
        )
        self._vccint_cnt = SamplingCounter("vccint", self._det_cc, unit="mV")
        self._vccaux_cnt = SamplingCounter("vccaux", self._det_cc, unit="mV")
        self._vpvn_cnt = SamplingCounter("vpvn", self._det_cc, unit="mV")
        self._vrefp_cnt = SamplingCounter("vrefp", self._det_cc, unit="mV")

        class Acquisition(Settings):
            """
            {
                'gain_type': 'sl',
                'pixel_depth': 6
            }
            """

            def __init__(self, device):
                self._device = device
                self._params = device._ctrl_params["det"]
                super().__init__(device._config, path=["smartpix", "acquisition"])

            @setting_property(default=12)
            def pixel_depth(self):
                return self._params["pixel_depth"]

            @pixel_depth.setter
            @typecheck
            def pixel_depth(self, value: int):
                self._params["pixel_depth"] = value

            def __info__(self):
                return "Acquisition:\n" + tabulate(self._params) + "\n\n"

        self.acquisition = Acquisition(device)

    def __info__(self):
        return "Smartpix\n" + self.acquisition.__info__()

    @property
    def counters(self):
        return [
            self._temperature_cnt,
            self._vccint_cnt,
            self._vccaux_cnt,
            self._vpvn_cnt,
            self._vrefp_cnt,
        ]

    @property
    def counter_groups(self):
        res = {}
        res["health"] = counter_namespace(
            [
                self._temperature_cnt,
                self._vccint_cnt,
                self._vccaux_cnt,
                self._vpvn_cnt,
                self._vrefp_cnt,
            ]
        )
        return res
