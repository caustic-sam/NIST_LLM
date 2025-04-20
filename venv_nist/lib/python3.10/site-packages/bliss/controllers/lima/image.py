# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numbers
import numpy

from typing import Optional
from collections.abc import Iterable

from bliss import global_map
from bliss.common.counter import Counter
from bliss.config.beacon_object import BeaconObject
from bliss.common.logtools import log_debug
from bliss.common.utils import typecheck
from bliss.common.tango import DevFailed

# ========== RULES of Tango-Lima ==================

# Lima rules and order of image transformations:

# 1) binning
# 2) flip
# 3) rotation
# 4) roi (expressed in the current state f(bin, flip, rot))

#  roi is defined in the current image referential (i.e roi = f(rot, flip, bin))
#  raw_roi is defined in the raw image referential (i.e with bin=1,1  flip=False,False, rot=0)
#  flip =  [Left-Right, Up-Down]

# ----------------- helpers for ROI coordinates (x,y,w,h) transformations (flip, rotation, binning) --------------


def current_coords_to_raw_coords(coords_list, img_size, flip, rotation, binning):
    if not isinstance(coords_list, numpy.ndarray):
        pts = numpy.array(coords_list)
    else:
        pts = coords_list.copy()

    w0, h0 = img_size

    # inverse rotation
    if rotation != 0:
        pts = calc_pts_rotation(pts, -rotation, (w0, h0))
        if rotation in [90, 270]:
            w0, h0 = img_size[1], img_size[0]

    # unflipped roi
    if flip[0]:
        pts[:, 0] = w0 - pts[:, 0]

    if flip[1]:
        pts[:, 1] = h0 - pts[:, 1]

    # unbinned roi
    xbin, ybin = binning
    if xbin != 1 or ybin != 1:
        pts[:, 0] = pts[:, 0] * xbin
        pts[:, 1] = pts[:, 1] * ybin

    return pts


def raw_coords_to_current_coords(
    raw_coords_list, raw_img_size, flip, rotation, binning
):
    if not isinstance(raw_coords_list, numpy.ndarray):
        pts = numpy.array(raw_coords_list)
    else:
        pts = raw_coords_list.copy()

    w0, h0 = raw_img_size

    # bin roi
    xbin, ybin = binning
    if xbin != 1 or ybin != 1:
        pts[:, 0] = pts[:, 0] / xbin
        pts[:, 1] = pts[:, 1] / ybin
        w0 = w0 / xbin
        h0 = h0 / ybin

    # flip roi
    if flip[0]:
        pts[:, 0] = w0 - pts[:, 0]

    if flip[1]:
        pts[:, 1] = h0 - pts[:, 1]

    # rotate roi
    if rotation != 0:
        pts = calc_pts_rotation(pts, rotation, (w0, h0))

    return pts


def raw_roi_to_current_roi(raw_roi, raw_img_size, flip, rotation, binning):
    x, y, w, h = raw_roi
    pts = [[x, y], [x + w, y + h]]
    pts = raw_coords_to_current_coords(pts, raw_img_size, flip, rotation, binning)
    x1, y1 = pts[0]
    x2, y2 = pts[1]
    x = min(x1, x2)
    y = min(y1, y2)
    w = abs(x2 - x1)
    h = abs(y2 - y1)

    return [round(x), round(y), round(w), round(h)]


def current_roi_to_raw_roi(current_roi, img_size, flip, rotation, binning):
    x, y, w, h = current_roi
    pts = [[x, y], [x + w, y + h]]
    pts = current_coords_to_raw_coords(pts, img_size, flip, rotation, binning)
    x1, y1 = pts[0]
    x2, y2 = pts[1]
    x = min(x1, x2)
    y = min(y1, y2)
    w = abs(x2 - x1)
    h = abs(y2 - y1)
    return [x, y, w, h]


def calc_pts_rotation(pts, angle, img_size):
    if not isinstance(pts, numpy.ndarray):
        pts = numpy.array(pts)

    # define the camera fullframe
    w0, h0 = img_size
    frame = numpy.array([[0, 0], [w0, h0]])

    # define the rotation matrix
    theta = numpy.deg2rad(angle) * -1  # Lima rotation is clockwise !
    R = numpy.array(
        [[numpy.cos(theta), -numpy.sin(theta)], [numpy.sin(theta), numpy.cos(theta)]]
    )

    new_frame = numpy.dot(frame, R)
    new_pts = numpy.dot(pts, R)

    # find new origin
    ox = numpy.amin(new_frame[:, 0])
    oy = numpy.amin(new_frame[:, 1])

    # apply new origin
    new_pts[:, 0] = new_pts[:, 0] - ox
    new_pts[:, 1] = new_pts[:, 1] - oy

    return new_pts


# -------------------------------------------------------------------------------------------


def _to_list(setting, value):
    if value is None:
        return None
    return list(value)


class LimaImageParameters(BeaconObject):
    def __init__(self, controller, name):
        config = controller._config_node
        super().__init__(config, name=name, share_hardware=False, path=["image"])
        # properly put in map, to have "parameters" under the corresponding Lima controller node
        # (and not in "controllers")
        global_map.register(self, parents_list=[controller], tag="image_parameters")

    binning = BeaconObject.property_setting(
        "binning", default=[1, 1], set_marshalling=_to_list, set_unmarshalling=_to_list
    )

    @binning.setter
    @typecheck
    def binning(self, value: Iterable[int]):
        log_debug(self, f"set binning {value}")
        assert len(value) == 2
        value = [int(value[0]), int(value[1])]
        return value

    flip = BeaconObject.property_setting(
        "flip",
        default=[False, False],
        set_marshalling=_to_list,
        set_unmarshalling=_to_list,
    )

    @flip.setter
    @typecheck
    def flip(self, value: Iterable[bool]):
        log_debug(self, f"set flip {value}")
        assert len(value) == 2
        value = [bool(value[0]), bool(value[1])]
        return value

    rotation = BeaconObject.property_setting("rotation", default="NONE")

    @rotation.setter
    def rotation(self, value):
        log_debug(self, f"set rotation {value}")
        if isinstance(value, int):
            value = str(value)
        if value == "0":
            value = "NONE"
        assert isinstance(value, str)
        assert value in ["NONE", "90", "180", "270"]
        return value

    _roi = BeaconObject.property_setting(
        "_roi",
        default=[0, 0, 0, 0],
        set_marshalling=_to_list,
        set_unmarshalling=_to_list,
    )

    @_roi.setter
    @typecheck
    def _roi(self, value: Iterable[numbers.Real]):
        """
        Raw roi values. It can be real numbers, because coming from
        a current_roi_to_raw_roi transformation.
        It should not be used directly has roi coordinates.
        Use raw_roi_to_current_roi to obtain the correct integer roi coordinates.
        """
        log_debug(self, f"set _roi {value}")
        assert len(value) == 4
        value = [value[0], value[1], value[2], value[3]]
        return value

    _cur_roi = BeaconObject.property_setting(
        "_cur_roi",
        default=[0, 0, 0, 0],
        set_marshalling=_to_list,
        set_unmarshalling=_to_list,
    )


class ImageCounter(Counter):
    def __init__(self, controller):
        self._proxy = controller._proxy
        self._max_size: Optional[tuple[int, int]] = None
        """Cache the detector max size"""

        super().__init__("image", controller)

        self._image_params = LimaImageParameters(
            controller, f"{controller._name_prefix}:image"
        )

    def _is_roi_active(self) -> bool:
        """True if the ROI have an effect on the image"""
        if (self.width, self.height) != self.fullsize:
            if self.roi != [0, 0, 0, 0]:
                return True
        return False

    def __info__(self):
        lines = []
        roi_active = ""
        if self._is_roi_active():
            roi_active = "(ROI active)"

        lines.append(
            f"size (w,h): {self.width}, {self.height} {roi_active} full size ({self.fullsize[0]}, {self.fullsize[1]})"
        )
        lines.append(f"depth:      {self.depth}")
        lines.append(f"bpp:        {self.bpp}")

        lines.append(f"binning:    {self.binning}")
        lines.append(f"flip:       {self.flip}")
        lines.append(f"rotation:   {self.rotation}")
        if roi_active != "":
            lines.append(f"roi:        {self.roi}")
        else:
            lines.append("roi:        None")

        return "\n".join(lines)

    @property
    def dtype(self):
        # Because it is a reference
        return None

    @property
    def shape(self):
        # Because it is a reference
        return (0, 0)

    # ------- Specific interface ----------------------------------

    @property
    def fullsize(self):
        """return the detector size taking into account the current binning and rotation"""

        w0, h0 = self._get_detector_max_size()

        xbin, ybin = self.binning
        w0 = int(w0 / xbin)
        h0 = int(h0 / ybin)

        if (abs(self.rotation) % 360) // 90 in [0, 2]:
            fw, fh = w0, h0
        else:
            fw, fh = h0, w0  # switch w and h if rotation in [90, 270]

        return fw, fh

    @property
    def depth(self):
        return self._proxy.image_sizes[1]

    @property
    def bpp(self):
        return self._proxy.image_type

    @property
    def width(self):
        return self.roi[2]

    @property
    def height(self):
        return self.roi[3]

    @property
    def binning(self):
        return self._image_params.binning

    @binning.setter
    def binning(self, value):
        self._image_params.binning = value
        self._update_cur_roi()

    @property
    def flip(self):
        return self._image_params.flip

    @flip.setter
    def flip(self, value):
        self._image_params.flip = value
        self._update_cur_roi()

    @property
    def rotation(self):
        if self._image_params.rotation == "NONE":
            return 0
        else:
            return int(self._image_params.rotation)

    @rotation.setter
    def rotation(self, value):
        self._image_params.rotation = value
        self._update_cur_roi()

    @property
    def raw_roi(self) -> tuple[int, int, int, int]:
        """
        Image ROI as defined in the raw image referential (i.e with bin=1,1  flip=False,False, rot=0).
        """
        self._initialize_roi()
        return self._image_params._roi

    @property
    def roi(self) -> tuple[int, int, int, int]:
        """Returns the image ROI as it is actually defined.

        This state count not be yet applied to the Lima device, but will be
        applied while preparing the next scan.
        """
        self._initialize_roi()
        return self._image_params._cur_roi

    @roi.setter
    def roi(self, value: Optional[tuple[int, int, int, int]]):
        if value is None:
            self._image_params._roi = [0, 0, 0, 0]
            return
        roi = self._check_roi_validity(value)
        self._sync_roi(roi, is_raw_roi=False)

    def _initialize_roi(self):
        """If the ROI have an undefined value, initialize it"""
        roi = self._image_params._roi
        # Also check on cur_roi in case of previous settings from bliss < 1.11
        cur_roi = self._image_params._cur_roi
        if roi == [0, 0, 0, 0] or cur_roi == [0, 0, 0, 0]:
            self.reset_roi()

    def reset_roi(self):
        """Reset the ROI from the hardware"""
        w0, h0 = self._get_detector_max_size()
        roi = [0, 0, w0, h0]
        self._sync_roi(roi, is_raw_roi=True)

    def _sync_roi(self, roi: tuple[int, int, int, int], is_raw_roi: bool):
        """
        Synchronize redis with a ROI.

        Arguments:
            roi: The ROI to set
            is_raw_roi: If true the ROI is a raw roi (without transformartion),
                        else it's the current ROI (after transformation)
        """
        if is_raw_roi:
            cur_roi = self._calc_cur_roi(roi)
            raw_roi = roi
        else:
            cur_roi = roi
            raw_roi = self._calc_raw_roi(roi)

        self._image_params._roi = raw_roi
        self._image_params._cur_roi = cur_roi
        self._counter_controller._update_lima_rois()

    @property
    def subarea(self):
        """Returns the active area of the detector (like 'roi').

        The rectangular area is defined by the top-left corner and bottom-right corner positions.
        Example: subarea = [x0, y0, x1, y1]
        """
        x, y, w, h = self.roi
        return [x, y, x + w, y + h]

    @subarea.setter
    def subarea(self, value):
        """Define a reduced active area on the detector chip (like 'roi').

        The rectangular area is defined by the top-left corner and bottom-right corner positions.
        Example: subarea = [x0, y0, x1, y1]
        """
        px0, py0, px1, py1 = value
        x0 = min(px0, px1)
        x1 = max(px0, px1)
        y0 = min(py0, py1)
        y1 = max(py0, py1)
        w = x1 - x0
        h = y1 - y0
        self.roi = [x0, y0, w, h]

    def _update_cur_roi(self, update_dependencies=True):
        """Update the current ROI location based on the new transformation

        Arguments:
            update_dependencies: If true, the ROI counter will be updated
        """
        raw_roi = self.raw_roi
        self._sync_roi(raw_roi, is_raw_roi=True)
        if update_dependencies:
            self._counter_controller._update_lima_rois()

    def _calc_raw_roi(
        self, roi: tuple[int, int, int, int]
    ) -> tuple[int, int, int, int]:
        """Computes the raw_roi from a given roi and current bin, flip, rot"""

        img_size = self.fullsize  #!!!! NOT _get_detector_max_size() !!!
        return current_roi_to_raw_roi(
            roi, img_size, self.flip, self.rotation, self.binning
        )

    def _calc_cur_roi(
        self, roi: tuple[int, int, int, int]
    ) -> tuple[int, int, int, int]:
        """Computes the cur_roi from a given roi and current bin, flip, rot"""
        img_size = self._get_detector_max_size()
        return raw_roi_to_current_roi(
            roi, img_size, self.flip, self.rotation, self.binning
        )

    def _read_detector_max_size(self) -> tuple[int, int]:
        """
        Read the max size of the Lima detector.

        raises:
            RuntimeError: If the detector is not reachable
        """
        log_debug(self, "get proxy.max_size")
        proxy = self._proxy
        try:
            proxy.ping()
        except DevFailed:
            log_debug(self, "Error while checking device", exc_info=True)
            raise RuntimeError(
                f"Lima tango device '{self._proxy.name()}' is not reachable"
            )
        w, h = self._proxy.image_max_dim
        return int(w), int(h)

    def _get_detector_max_size(self) -> tuple[int, int]:
        """Return the max size of this detector.

        It is the raw value without considering binning and rotation
        """
        if self._max_size is None:
            self._max_size = self._read_detector_max_size()
        return self._max_size

    def _check_roi_validity(self, roi: tuple[int, int, int, int]):
        """Check if the roi coordinates are valid, else trim the roi to fits image size"""

        w0, h0 = self.fullsize
        x, y, w, h = roi

        if w == 0:
            w = w0

        if h == 0:
            h = h0

        # bx = x < 0 or x >= w0
        # by = y < 0 or y >= h0
        # bw = w < 1 or (x + w) > w0
        # bh = h < 1 or (y + h) > h0

        # if bx or by or bw or bh:
        #     raise ValueError(
        #         f"the given roi {roi} is not fitting the current image size {(w0, h0)}"
        #     )

        # --- In case we don t want to raise an error
        # --- we can just trim the roi so that it fits the current image size
        x = max(x, 0)
        x = min(x, w0 - 1)
        y = max(y, 0)
        y = min(y, h0 - 1)
        w = max(w, 1)
        w = min(w, w0 - x)
        h = max(h, 1)
        h = min(h, h0 - y)

        return [int(x), int(y), int(w), int(h)]

    def to_dict(self):
        return {
            "image_bin": self.binning,
            "image_flip": self.flip,
            "image_rotation": self._image_params.rotation,  # as str (to apply to proxy)
            "image_roi": self.roi,
        }

    def get_geometry(self):
        w, h = self.fullsize
        return {
            "fullwidth": w,
            "fullheight": h,
            "binning": self.binning,
            "flip": self.flip,
            "rotation": self.rotation,
            "roi": self.roi,
        }

    def set_geometry(
        self,
        binning: tuple[int, int],
        flip: tuple[bool, bool],
        rotation: int,
        roi: Optional[tuple[int, int, int, int]] = None,
    ):
        """Update the transformation.

        Arguments:
            binning: The new binning to use
            flip: The new flip to use
            rotation: The new flip to use
            roi: An optional new ROI to use
        """
        self._image_params.binning = binning
        self._image_params.flip = flip
        self._image_params.rotation = rotation
        if roi is None:
            self._update_cur_roi()
        else:
            self._update_cur_roi(update_dependencies=False)
            self.roi = roi

    def update_max_size(self):
        """Update the image maximum size (from the Lima device).

        If the local size have to be updated, the image ROI is reset to the full
        frame.
        """
        max_size = self._read_detector_max_size()
        if self._max_size == max_size:
            return
        self._max_size = max_size
        self.reset_roi()
