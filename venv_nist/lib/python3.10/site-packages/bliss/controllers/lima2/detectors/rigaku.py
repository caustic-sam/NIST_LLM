# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


from bliss.controllers.lima2.tabulate import tabulate
from bliss.controllers.lima2.controller import (
    DetectorController,
)
from bliss.controllers.lima2.settings import Settings


class Detector:
    """Rigaku detector user interface"""

    def __init__(self, device):
        self._frame_cc = DetectorController(device)

        class Acquisition(Settings):
            """
            {
                'thresholds': [{
                    'energy': 4020.5,
                    'enabled': True
                }, {
                    'energy': 4020.5,
                    'enabled': True
                }],
                'trigger_start_delay': 0.0,
                'roi': 'full',
                'nb_pipeline_threads': 1
            }
            """

            def __init__(self, device):
                self._device = device
                self._params = device._ctrl_params["det"]
                super().__init__(device._config, path=["rigaku", "acquisition"])

            def __info__(self):
                return "Acquisition:\n" + tabulate(self._params) + "\n\n"

        class Experiment(Settings):
            def __init__(self, device):
                self._device = device
                self._params = device._ctrl_params["exp"]
                super().__init__(device._config, path=["dectris", "experiment"])

            def __info__(self):
                return "Experiment:\n" + tabulate(self._params) + "\n\n"

        self.acquisition = Acquisition(device)
        # self.experiment = Experiment(device)

    def __info__(self):
        return (
            self.acquisition.__info__()
            # + self.experiment.__info__()
        )

    @property
    def counters(self):
        return []

    @property
    def counter_groups(self):
        res = {}
        return res
