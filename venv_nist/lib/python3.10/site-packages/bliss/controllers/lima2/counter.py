# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import enum
import numpy as np

from bliss.common.counter import Counter, IntegratingCounter
from bliss.controllers.lima.roi import Roi


class FrameCounter(Counter):
    def __init__(self, name, controller, saving_spec, file_only=False):
        self._max_width = 0
        self._max_height = 0
        self._saving_spec = saving_spec
        self._file_only = file_only

        super().__init__(name, controller)

    def __info__(self):
        return "width:    Unknown\n" "height:   Unknown\n" "bpp:      Unknown\n"

    @property
    def shape(self):
        # Required by scan_info["channels"][xxx].dim
        return (0, 0, 0)

    @property
    def saving_spec(self):
        """The path to the saving parameters"""
        return self._saving_spec

    @property
    def file_only(self):
        """True if the file is accessible only on file"""
        return self._file_only


class RoiStat(enum.Enum):
    Sum = "sum"
    Avg = "avg"
    Std = "std"
    Min = "min"
    Max = "max"


class RoiStatCounter(IntegratingCounter):
    """A Counter object used for the statistics counters associated to one Roi"""

    def __init__(self, roi: Roi, stat: RoiStat, controller, dtype, **kwargs):
        self.roi = roi
        self.stat = stat
        name = f"{roi.name}_{stat.name.lower()}"
        super().__init__(name, controller, dtype=dtype, **kwargs)

    def scan_metadata(self) -> dict:
        metadata = super().scan_metadata()
        metadata.update({self.roi.name: self.roi.to_dict()})
        return metadata


class RoiStatCounters:
    """An iterable container (associated to one roi.name) that yield the RoiStatCounters"""

    def __init__(self, roi: Roi, controller, **kwargs):
        self._sum = RoiStatCounter(roi, RoiStat.Sum, controller, np.float64, **kwargs)
        self._avg = RoiStatCounter(roi, RoiStat.Avg, controller, np.float32, **kwargs)
        self._std = RoiStatCounter(roi, RoiStat.Std, controller, np.float32, **kwargs)
        self._min = RoiStatCounter(roi, RoiStat.Min, controller, np.float32, **kwargs)
        self._max = RoiStatCounter(roi, RoiStat.Max, controller, np.float32, **kwargs)

    @property
    def sum(self):
        return self._sum

    @property
    def avg(self):
        return self._avg

    @property
    def std(self):
        return self._std

    @property
    def min(self):
        return self._min

    @property
    def max(self):
        return self._max

    def __iter__(self):
        # yield self.sum
        yield self.avg
        yield self.std
        yield self.min
        yield self.max
        yield self.sum


class RoiProfCounter(IntegratingCounter):
    """A Counter object used for the profile counters associated to one Roi"""

    def __init__(self, roi: Roi, stat: RoiStat, controller, dtype, **kwargs):
        self.roi = roi
        self.stat = stat
        name = f"{roi.name}_{stat.name.lower()}"
        super().__init__(name, controller, dtype=dtype, **kwargs)

    def scan_metadata(self) -> dict:
        metadata = super().scan_metadata()
        metadata.update({self.roi.name: self.roi.to_dict()})
        return metadata

    @property
    def shape(self):
        """The data shape as used by numpy."""

        if self.roi.mode == "vertical":
            shape = (self.roi.height,)
        elif self.roi.mode == "horizontal":
            shape = (self.roi.width,)
        else:
            assert False, "Invalid profile mode"

        return shape


class RoiProfCounters:
    """An iterable container (associated to one roi.name) that yield the RoiProfCounters"""

    def __init__(self, roi: Roi, controller, **kwargs):
        self._sum = RoiProfCounter(roi, RoiStat.Sum, controller, np.float64, **kwargs)
        self._avg = RoiProfCounter(roi, RoiStat.Avg, controller, np.float32, **kwargs)
        self._std = RoiProfCounter(roi, RoiStat.Std, controller, np.float32, **kwargs)
        self._min = RoiProfCounter(roi, RoiStat.Min, controller, np.float32, **kwargs)
        self._max = RoiProfCounter(roi, RoiStat.Max, controller, np.float32, **kwargs)

    @property
    def sum(self):
        return self._sum

    @property
    def avg(self):
        return self._avg

    @property
    def std(self):
        return self._std

    @property
    def min(self):
        return self._min

    @property
    def max(self):
        return self._max

    def __iter__(self):
        # yield self.sum
        yield self.avg
        yield self.std
        yield self.min
        yield self.max
        yield self.sum
