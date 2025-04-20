# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numpy
from bliss.common.counter import Counter


class SpectrumCounter(Counter):
    def __init__(self, name, controller, conversion_function=None, unit=None):
        super().__init__(name, controller, conversion_function, unit)

    @property
    def dtype(self):
        return numpy.uint32

    @property
    def shape(self):
        return (self._counter_controller._mca._spectrum_size,)

    def __info__(self):
        info_str = super().__info__()
        info_str += f" spectrum size: {self.shape[0]}\n"
        return info_str


class ROICounter(Counter):
    """A counter for ROI data"""

    def __init__(self, mca_roi, controller, conversion_function=None, unit=None):
        super().__init__(mca_roi.name, controller, conversion_function, unit)
        self._mca_roi = mca_roi

    @property
    def roi(self):
        return self._mca_roi

    @property
    def dtype(self):
        return numpy.int64

    @property
    def shape(self):
        return ()

    def __info__(self):
        info_str = super().__info__()
        info_str += f" roi: [{self.roi.start}:{self.roi.stop}]\n"
        info_str += f" channel: {self.roi.channel}\n"
        return info_str


class StatCounter(Counter):
    """A counter for statistics data"""

    def __init__(
        self,
        name,
        label_index,
        controller,
        conversion_function=None,
        unit=None,
    ):
        super().__init__(name, controller, conversion_function, unit)

        self._label_index = label_index

    @property
    def label_index(self):
        return self._label_index

    @property
    def dtype(self):
        return numpy.float64

    @property
    def shape(self):
        return ()

    def __info__(self):
        info_str = super().__info__()
        return info_str
