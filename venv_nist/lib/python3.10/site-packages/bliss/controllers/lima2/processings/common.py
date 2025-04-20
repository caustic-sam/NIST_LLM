# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


from __future__ import annotations

from bliss.common.utils import typecheck
import bliss.common.plot as plot_module  # for edit_rois

from bliss.shell.formatters.table import IncrementalTable

from bliss.controllers.lima.roi import Roi, ArcRoi, RoiProfile

from bliss.controllers.lima2.tabulate import tabulate
from bliss.controllers.lima2.settings import Settings, setting_property
from bliss.controllers.lima2.counter import RoiStatCounters, RoiProfCounters
from bliss.controllers.lima2.controller import RoiStatController, RoiProfilesController


class Saving(Settings):
    def __init__(self, config, path, params, title=""):
        self._params = params
        self._title = title
        super().__init__(config, path)

    @setting_property(default=False)
    def enabled(self):
        return self._params["enabled"]

    @enabled.setter
    @typecheck
    def enabled(self, value: bool):
        self._params["enabled"] = value

    @setting_property(default="bshuf_lz4")
    def compression(self):
        return self._params["compression"]

    @compression.setter
    @typecheck
    def compression(self, value: str):
        self._params["compression"] = value

    @setting_property(default=50)
    def nb_frames_per_file(self):
        return self._params["nb_frames_per_file"]

    @nb_frames_per_file.setter
    @typecheck
    def nb_frames_per_file(self, value: int):
        self._params["nb_frames_per_file"] = value

    @setting_property(default="abort")
    def file_exists_policy(self):
        return self._params["file_exists_policy"]

    @file_exists_policy.setter
    @typecheck
    def file_exists_policy(self, value: str):
        self._params["file_exists_policy"] = value

    def __info__(self):
        header = "Saving%s\n" % (f" {self._title}" if self._title else "")
        return header + tabulate(self._params)


class RoiStatistics(Settings):
    def __init__(self, config, path, params):
        self._params = params
        self._rois = []
        super().__init__(config, path)

    @setting_property
    def rois(self) -> list:
        return self._rois

    @rois.setter
    @typecheck
    def rois(self, values: list):
        # Check for uniqueness of the ROI names
        names = [roi.name for roi in values]
        if len(names) > len(set(names)):
            raise ValueError("Duplicated ROI name.")

        self._rois = values

        self._params["rect_rois"] = [
            {
                "topleft": {"x": roi.x, "y": roi.y},
                "dimensions": {"x": roi.width, "y": roi.height},
            }
            for roi in values
            if type(roi) is Roi
        ]

        self._params["arc_rois"] = [
            {
                "center": {"x": roi.cx, "y": roi.cy},
                "r1": roi.r1,
                "r2": roi.r2,
                "a1": roi.a1,
                "a2": roi.a2,
            }
            for roi in values
            if type(roi) is ArcRoi
        ]

    @staticmethod
    def _create_roi(name: str, roi: list | Roi | ArcRoi) -> Roi | ArcRoi:
        if isinstance(
            roi,
            (
                Roi,
                ArcRoi,
            ),
        ):
            roi = roi
            roi._name = name
        elif len(roi) == 4:
            try:
                roi = Roi(*roi, name=name)
            except Exception:
                roi = None
        elif len(roi) == 6:
            try:
                roi = ArcRoi(*roi, name=name)
            except Exception:
                roi = None
        return roi

    def _add_rois(self, rois: list):
        if not isinstance(rois, list):
            rois = [
                rois,
            ]

        names = set([roi.name for roi in self._rois + rois])
        if len(self._rois) + len(rois) > len(names):
            duplicates = [
                name for name in names if [roi.name for roi in rois].count(name) > 1
            ]
            raise KeyError(f"Duplicated ROI name {duplicates}")

        self._rois = self._rois + rois

    def _remove_rois(self, names: tuple | str):
        if not isinstance(names, tuple):
            names = (names,)

        rois = self._rois

        indexes = [i for i, roi in enumerate(rois) if roi.name in names]
        if len(indexes) < len(names):
            missing = list(set(names) - set([roi.name for roi in rois]))
            raise KeyError(f"{missing}")

        for index in indexes:
            del rois[index]

        self._rois = rois

    def _get_rois(self, names: str | tuple[str, ...]):
        if isinstance(names, str):
            names = (names,)

        rois = [roi for roi in self._rois if roi.name in names]

        if len(rois) < len(names):
            missing = list(set(names) - set([roi.name for roi in rois]))
            raise KeyError(f"{missing}")

        return rois

    # dict like API
    # @typecheck
    def set(self, name: str, roi: list | Roi | ArcRoi):
        self._add_rois(RoiStatistics._create_roi(name, roi))

    # @typecheck
    def get(self, name: str, default=None) -> Roi | ArcRoi:
        res = self._get_rois(name)
        if not res:
            res = default

        return res[0]

    # @typecheck
    def __getitem__(self, names: str | tuple[str, ...]) -> list | Roi | ArcRoi:
        return self._get_rois(names)

    # @typecheck
    def __setitem__(self, names: str | tuple[str, ...], rois: str | tuple[str, ...]):
        self._add_rois(
            [RoiStatistics._create_roi(name, roi) for name, roi in zip(names, rois)]
        )

    # @typecheck
    def __delitem__(self, names: str | tuple[str, ...]):
        self._remove_rois(names)

    def __info__(self):
        header = "ROI Statistics\n"
        if self._rois:
            labels = ["Name", "Parameters", "State"]
            tab = IncrementalTable([labels])

            for roi in self._rois:
                tab.add_line(
                    [
                        roi.name,
                        str(roi),
                        "Enabled" if self._params["enabled"] else "Disabled",
                    ]
                )

            tab.resize(minwidth=10, maxwidth=100)
            tab.add_separator(sep="-", line_index=1)
            return header + str(tab) + "\n"
        else:
            return header + "*** no ROIs defined ***" + "\n"


class RoiProfiles(Settings):
    def __init__(self, config, path, params):
        self._params = params
        self._rois = []
        super().__init__(config, path)

    @setting_property
    def rois(self) -> list:
        return self._rois

    @rois.setter
    @typecheck
    def rois(self, values: list[RoiProfile]):
        # Check for uniqueness of the ROI names
        names = [roi.name for roi in values]
        if len(names) > len(set(names)):
            raise ValueError("Duplicated ROI name.")

        self._rois = values

        self._params["rois"] = [
            {
                "topleft": {"x": roi.x, "y": roi.y},
                "dimensions": {"x": roi.width, "y": roi.height},
            }
            for roi in values
            if type(roi) is RoiProfile
        ]

        self._params["directions"] = [
            roi.mode for roi in values if type(roi) is RoiProfile
        ]

    @staticmethod
    def _create_roi(name: str, roi: list | Roi | ArcRoi) -> Roi | ArcRoi:
        if isinstance(
            roi,
            (
                Roi,
                ArcRoi,
            ),
        ):
            roi = roi
            roi._name = name
        elif len(roi) == 4:
            try:
                roi = Roi(*roi, name=name)
            except Exception:
                roi = None
        elif len(roi) == 6:
            try:
                roi = ArcRoi(*roi, name=name)
            except Exception:
                roi = None
        return roi

    def _add_rois(self, rois: list):
        if not isinstance(rois, list):
            rois = [
                rois,
            ]

        names = set([roi.name for roi in self._rois + rois])
        if len(self._rois) + len(rois) > len(names):
            duplicates = [
                name for name in names if [roi.name for roi in rois].count(name) > 1
            ]
            raise KeyError(f"Duplicated ROI name {duplicates}")

        self._rois = self._rois + rois

    def _remove_rois(self, names: tuple | str):
        if not isinstance(names, tuple):
            names = (names,)

        rois = self._rois

        indexes = [i for i, roi in enumerate(rois) if roi.name in names]
        if len(indexes) < len(names):
            missing = list(set(names) - set([roi.name for roi in rois]))
            raise KeyError(f"{missing}")

        for index in indexes:
            del rois[index]

        self._rois = rois

    def _get_rois(self, names: str | tuple[str, ...]):
        if isinstance(names, str):
            names = (names,)

        rois = [roi for roi in self._rois if roi.name in names]

        if len(rois) < len(names):
            missing = list(set(names) - set([roi.name for roi in rois]))
            raise KeyError(f"{missing}")

        return rois

    # dict like API
    # @typecheck
    def set(self, name: str, roi: list | RoiProfile):
        self._add_rois(RoiProfiles._create_roi(name, roi))

    # @typecheck
    def get(self, name: str, default=None) -> RoiProfile:
        res = self._get_rois(name)
        if not res:
            res = default

        return res[0]

    # @typecheck
    def __getitem__(self, names: str | tuple[str, ...]) -> list | RoiProfile:
        return self._get_rois(names)

    # @typecheck
    def __setitem__(self, names: str | tuple[str, ...], rois: str | tuple[str, ...]):
        self._add_rois(
            [RoiProfiles._create_roi(name, roi) for name, roi in zip(names, rois)]
        )

    # @typecheck
    def __delitem__(self, names: str | tuple[str, ...]):
        self._remove_rois(names)

    def __info__(self):
        header = "ROI Profiles\n"
        if self._rois:
            labels = ["Name", "Parameters", "State"]
            tab = IncrementalTable([labels])

            for roi in self._rois:
                tab.add_line(
                    [
                        roi.name,
                        str(roi),
                        "Enabled" if self._params["enabled"] else "Disabled",
                    ]
                )

            tab.resize(minwidth=10, maxwidth=100)
            tab.add_separator(sep="-", line_index=1)
            return header + str(tab) + "\n"
        else:
            return header + "*** no ROIs defined ***" + "\n"


class HasRoi:
    def _init_with_device(self, device):
        self._roi_counters_cc = RoiStatController(
            device, master_controller=device._frame_cc
        )

        self._roi_profiles_cc = RoiProfilesController(
            device, master_controller=device._frame_cc
        )

    def _get_roi_counters(self):
        res = []

        if self.use_roi_stats:
            rois = self.roi_stats.rois
            for roi in rois:
                res.extend(RoiStatCounters(roi, self._roi_counters_cc))
            self._roi_counters_cc._rois = rois
        if self.use_roi_profiles:
            rois = self.roi_profiles.rois
            for roi in rois:
                res.extend(RoiProfCounters(roi, self._roi_profiles_cc))
            self._roi_profiles_cc._rois = rois

        return res

    def edit_rois(self):
        """
        Edit this detector ROI counters with Flint.

        When called without arguments, it will use the image from specified detector
        from the last scan/ct as a reference. If `acq_time` is specified,
        it will do a `ct()` with the given count time to acquire a new image.

        .. code-block:: python

            # Flint will be open if it is not yet the case
            pilatus1.edit_rois(0.1)

            # Flint must already be open
            ct(0.1, pilatus1)
            pilatus1.edit_rois()
        """
        # Check that Flint is already there
        flint = plot_module.get_flint()

        # def update_image_in_plot():
        #     """Create a single frame from detector data if available
        #     else use a placeholder.
        #     """
        #     try:
        #         image_data = image_utils.image_from_server(self._proxy, -1)
        #         data = image_data.array
        #     except Exception:
        #         # Else create a checker board place holder
        #         y, x = np.mgrid[0 : self.image.height, 0 : self.image.width]
        #         data = ((y // 16 + x // 16) % 2).astype(np.uint8) + 2
        #         data[0, 0] = 0
        #         data[-1, -1] = 5

        #     channel_name = f"{self.name}:frame"
        #     flint.set_static_image(channel_name, data)

        # That it contains an image displayed for this detector
        plot_proxy = flint.get_live_plot(image_detector=self._device.name)
        ranges = plot_proxy.get_data_range()
        if ranges[0] is None:
            # update_image_in_plot()
            pass
        plot_proxy.focus()

        # Retrieve all the ROIs
        selection = []
        selection.extend(self.roi_stats.rois)
        selection.extend(self.roi_profiles.rois)

        print(f"Waiting for ROI edition to finish on {self._device.name}...")
        selection = plot_proxy.select_shapes(
            selection,
            kinds=[
                "lima-rectangle",
                "lima-arc",
                "lima-vertical-profile",
                "lima-horizontal-profile",
            ],
        )

        self.roi_stats.rois = [roi for roi in selection if type(roi) in (Roi, ArcRoi)]
        self.roi_profiles.rois = [roi for roi in selection if type(roi) is RoiProfile]

        roi_string = ", ".join(sorted([s.__repr__() for s in selection]))
        print(f"Applied ROIS {roi_string} to {self._device.name}")
