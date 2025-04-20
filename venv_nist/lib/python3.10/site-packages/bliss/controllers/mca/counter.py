from functools import lru_cache
import collections
import enum
import numpy

from bliss.common.counter import Counter
from bliss.controllers.counter import counter_namespace

AcquisitionMode = enum.Enum("AcquisitionMode", "MCA HWSCA")

Stats = collections.namedtuple(
    "Stats",
    "realtime trigger_livetime energy_livetime triggers events icr ocr deadtime",
)


class BaseMcaCounter(Counter):
    def __init__(self, mca, base_name, detector=None):
        self.mca = mca
        self.acquisition_device = None
        self.detector_channel = detector
        self.base_name = base_name

        super().__init__(base_name, mca)

    @property
    def name(self):
        if self.detector_channel is None:
            return self.base_name
        return "{}_det{}".format(self.base_name, self.detector_channel)

    # Extra logic
    def register_device(self, device):
        # Current device
        self.acquisition_device = device
        # Consistency checks
        assert self._counter_controller is self.acquisition_device.device
        if self.detector_channel is not None:
            assert self.detector_channel in self._counter_controller.elements

    def extract_point(self, spectrums, stats):
        raise NotImplementedError

    def feed_point(self, spectrums, stats):
        point = self.extract_point(spectrums, stats)
        return point


class StatisticsMcaCounter(BaseMcaCounter):
    def __init__(self, mca, stat_name, detector):
        self.stat_name = stat_name
        assert stat_name in Stats._fields
        super(StatisticsMcaCounter, self).__init__(mca, stat_name, detector)

    @property
    def dtype(self):
        if self.stat_name in ("triggers", "events"):
            return int
        return float

    def extract_point(self, spectrums, stats):
        return getattr(stats[self.detector_channel], self.stat_name)


class SpectrumMcaCounter(BaseMcaCounter):
    def __init__(self, mca, detector):
        super(SpectrumMcaCounter, self).__init__(mca, "spectrum", detector)

    @property
    def dtype(self):
        return numpy.uint32

    @property
    def shape(self):
        if self.acquisition_device is None:
            return (self._counter_controller.spectrum_size,)
        return (self.acquisition_device.spectrum_size,)

    def extract_point(self, spectrums, stats):
        return spectrums[self.detector_channel]


class RoiMcaCounter(BaseMcaCounter):
    def __init__(self, mca, roi_name, detector):
        self.roi_name = roi_name
        self.start_index, self.stop_index = None, None
        super(RoiMcaCounter, self).__init__(mca, roi_name, detector)

    def register_device(self, device):
        super(RoiMcaCounter, self).register_device(device)
        self.start_index, self.stop_index = self.mca.rois.get(self.roi_name)

    @property
    def dtype(self):
        return int

    def compute_roi(self, spectrum):
        return sum(spectrum[self.start_index : self.stop_index])

    def extract_point(self, spectrums, stats):
        return self.compute_roi(spectrums[self.detector_channel])


class RoiSumMcaCounter(RoiMcaCounter):
    def __init__(self, mca, roi_name):
        super(RoiSumMcaCounter, self).__init__(mca, roi_name, None)

    def extract_point(self, spectrums, stats):
        return sum(map(self.compute_roi, spectrums.values()))


class RoiIntegralCounter(BaseMcaCounter):
    def __init__(self, mca, roi_name, detector):
        self.roi_name = roi_name
        self.start_index, self.stop_index = None, None
        super(RoiIntegralCounter, self).__init__(mca, roi_name, detector)

    def register_device(self, device):
        super(RoiIntegralCounter, self).register_device(device)
        self.start_index = 0
        self.stop_index = self.acquisition_device.spectrum_size - 1

    def extract_point(self, spectrums, stats):
        return sum(spectrums[self.detector_channel][:])


@lru_cache(None)
def mca_counters(mca):
    """Provide a flat access to all MCA counters.

    - counters.spectrum_det<N>
    - counters.realtime_det<N>
    - counters.trigger_livetime_det<N>
    - counters.energy_livetime_det<N>
    - counters.triggers_det<N>
    - counters.events_det<N>
    - counters.icr_det<N>
    - counters.ocr_det<N>
    - counters.deadtime_det<N>
    """
    # Rois
    counters = [
        RoiMcaCounter(mca, roi, element)
        for element in mca.elements
        for roi in mca.rois.get_names()
    ]
    if mca.acquisition_mode == AcquisitionMode.HWSCA:
        if not len(counters):
            counters += [
                RoiIntegralCounter(mca, "counts", element) for element in mca.elements
            ]
    if mca.acquisition_mode == AcquisitionMode.MCA:
        # Spectrum
        counters += [SpectrumMcaCounter(mca, element) for element in mca.elements]
        # Stats
        counters += [
            StatisticsMcaCounter(mca, stat, element)
            for element in mca.elements
            for stat in Stats._fields
        ]

        # Roi sums
        if len(mca.elements) > 1:
            counters += [RoiSumMcaCounter(mca, roi) for roi in mca.rois.get_names()]

    # Instantiate
    return counter_namespace(counters)


@lru_cache(None)
def mca_counter_groups(mca):
    """Provide a group access to MCA counters.

    - groups.spectrum
    - groups.realtime
    - groups.trigger_livetime
    - groups.energy_livetime
    - groups.triggers
    - groups.events
    - groups.icr
    - groups.ocr
    - groups.deadtime
    - groups.det<N>
    """
    dct = {}
    counters = mca_counters(mca)
    roi_names = list(mca.rois.get_names())

    # Prefix groups
    prefixes = list(Stats._fields) + ["spectrum"] + roi_names
    for prefix in prefixes:
        dct[prefix] = counter_namespace(
            [counter for counter in counters if counter.name.startswith(prefix)]
        )

    # Suffix groups
    suffixes = ["det{}".format(e) for e in mca.elements]
    for suffix in suffixes:
        dct[suffix] = counter_namespace(
            [counter for counter in counters if counter.name.endswith(suffix)]
        )

    # Instantiate group namespace
    return counter_namespace(dct)
