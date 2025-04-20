# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import time
from bliss.common.plot import get_flint

# ======== SPECTROMETER PLOTS =======================================


class SpectroPlot:
    def __init__(self, spectro):
        self._spectro = spectro
        self._min_refresh_time = 0.3
        self._last_refresh_time = time.perf_counter()
        self.create_plot()

    def create_plot(self):
        self.plot = get_flint().get_plot(
            "spectroplot", self._spectro.name, self._spectro.name, selected=True
        )
        d = self._spectro.detector.target.radius * 2
        self.plot.set_box_min_max([-d, -d, -d], [d, d, d])
        self.plot.set_data(**self._spectro._get_plot_data())

    def is_active(self):
        return self.plot.is_open()

    def update_plot(self, forced=False):
        if self.plot.is_open():
            now = time.perf_counter()
            dt = now - self._last_refresh_time
            if forced or (dt >= self._min_refresh_time):
                self._last_refresh_time = now
                self.plot.set_data(**self._spectro._get_plot_data())
