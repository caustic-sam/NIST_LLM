# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Contains Lima ROI geometries.

This have to have no dependecies in order to be imported from outside of BLISS.
"""

import enum
import numpy


class _BaseRoi:
    def __init__(self, name=None):
        self._name = name
        self.check_validity()

    @property
    def name(self):
        return self._name

    def check_validity(self):
        raise NotImplementedError

    def bounding_box(self):
        """Returns the bounding box of this shape.

        Returns:
            A list containing the minimal and the maximal coord of the bounding
            box of this shape
        """
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError

    def __eq__(self, other):
        raise NotImplementedError

    def get_params(self):
        """Return the list of parameters received at init"""
        raise NotImplementedError

    def to_dict(self):
        """Return typical info as a dict"""
        raise NotImplementedError


class Roi(_BaseRoi):
    def __init__(self, x, y, width, height, name=None):

        self._x = int(x)
        self._y = int(y)
        self._width = int(width)
        self._height = int(height)

        super().__init__(name)

        self._p0 = (self._x, self._y)
        self._p1 = (self._x + self._width, self._y + self._height)

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def p0(self):
        """return coordinates of the top left corner"""
        return self._p0

    @property
    def p1(self):
        """Return coordinates of the bottom right corner"""
        return self._p1

    def check_validity(self):
        if self._width <= 0:
            raise ValueError(f"Roi {self.name}: width must be > 0, not {self._width}")

        if self._height <= 0:
            raise ValueError(f"Roi {self.name}: height must be > 0, not {self._height}")

    def bounding_box(self):
        return [[self.x, self.y], [self.x + self.width, self.y + self.height]]

    def __repr__(self):
        return "<%s,%s> <%s x %s>" % (self.x, self.y, self.width, self.height)

    def __eq__(self, other):
        if other.__class__ != self.__class__:
            return False
        ans = self.x == other.x and self.y == other.y
        ans = ans and self.width == other.width and self.height == other.height
        ans = ans and self.name == other.name
        return ans

    def get_params(self):
        """Return the list of parameters received at init"""
        return [self.x, self.y, self.width, self.height]

    def to_dict(self):
        """Return typical info as a dict"""
        return {
            "kind": "rect",
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


class ArcRoi(_BaseRoi):
    """Arc ROI defined by few coordinates.

    The radius are not ordered, this can be the minimal then the maximal or
    the reverse.

    The angles are in degree. The range range is oriented in both value and
    direction:

    - `a1=10, a2=50` is a valid arc of a range of 40 degree
    - `a1=50, a2=10` is a not a valid shape
    - `a1=350, a2=370` is a valid arc of a range of 20 degree
    - `a1=350, a2=10` is not a valid shape

    Arguments:
        cx: X-center of circle defining the arc
        cy: Y-center of circle defining the arc
        r1: Start radius of the arc (can be the minimal or the maximal radius)
        r2: End radius of the arc (can be the minimal or the maximal radius)
        a1: Start angle of the arc (in degree)
        a2: End angle of the arc (in degree, bigger than a1)
    """

    def __init__(self, cx, cy, r1, r2, a1, a2, name=None):

        if a1 > a2:
            raise ValueError("a1 must be bigger than a2")

        self._cx = cx
        self._cy = cy
        self._r1 = min(r1, r2)
        self._r2 = max(r1, r2)
        self._a1 = a1
        self._a2 = a2

        super().__init__(name)

        self._a3 = a3 = (a1 + a2) / 2  # i.e: a3 = a1 + (a2-a1)/2
        self._aperture = abs(self.a2 - self.a1) / 2
        self._ratio = self.r1 / self.r2

        ca1, ca2, ca3 = (
            numpy.cos(numpy.deg2rad(a1)),
            numpy.cos(numpy.deg2rad(a2)),
            numpy.cos(numpy.deg2rad(a3)),
        )
        sa1, sa2, sa3 = (
            numpy.sin(numpy.deg2rad(a1)),
            numpy.sin(numpy.deg2rad(a2)),
            numpy.sin(numpy.deg2rad(a3)),
        )

        self._p0 = (cx, cy)
        self._p1 = (r1 * ca1 + cx, r1 * sa1 + cy)
        self._p2 = (r2 * ca1 + cx, r2 * sa1 + cy)
        self._p3 = (r2 * ca2 + cx, r2 * sa2 + cy)
        self._p4 = (r1 * ca2 + cx, r1 * sa2 + cy)
        self._p5 = (r2 * ca3 + cx, r2 * sa3 + cy)

    @property
    def cx(self):
        return self._cx

    @property
    def cy(self):
        return self._cy

    @property
    def r1(self):
        """The minimal radius"""
        return self._r1

    @property
    def r2(self):
        """The maximal radius"""
        return self._r2

    @property
    def a1(self):
        return self._a1

    @property
    def a2(self):
        return self._a2

    @property
    def a3(self):
        return self._a3

    @property
    def aperture(self):
        return self._aperture

    @property
    def ratio(self):
        return self._ratio

    @property
    def p0(self):
        """Return coordinates of the arc center"""
        return self._p0

    @property
    def p1(self):
        """Return coordinates of the point at (r1, a1)"""
        return self._p1

    @property
    def p2(self):
        """Return coordinates of the point at (r2, a1)"""
        return self._p2

    @property
    def p3(self):
        """Return coordinates of the point at (r2, a2)"""
        return self._p3

    @property
    def p4(self):
        """Return coordinates of the point at (r1, a2)"""
        return self._p4

    @property
    def p5(self):
        """Return coordinates of the point at (r2, a1 + (a2 - a1) / 2)"""
        return self._p5

    def check_validity(self):
        if self._r1 < 0:
            raise ValueError(
                f"ArcRoi {self.name}: first radius must be >= 0, not {self._r1}"
            )

        if self._r2 < self._r1:
            raise ValueError(
                f"ArcRoi {self.name}: second radius must be >= first radius, not {self._r2}"
            )

        if self._a1 == self._a2:
            raise ValueError(
                f"ArcRoi {self.name}: first and second angles must be different"
            )

    def __repr__(self):
        return "<%.1f, %.1f> <%.1f, %.1f> <%.1f, %.1f>" % (
            self.cx,
            self.cy,
            self.r1,
            self.r2,
            self.a1,
            self.a2,
        )

    def __eq__(self, other):
        if other.__class__ != self.__class__:
            return False
        ans = self.cx == other.cx and self.cy == other.cy
        ans = ans and self.r1 == other.r1 and self.r2 == other.r2
        ans = ans and self.a1 == other.a1 and self.a2 == other.a2
        ans = ans and self.name == other.name
        return ans

    def get_params(self):
        """Return the list of parameters received at init"""
        return [self.cx, self.cy, self.r1, self.r2, self.a1, self.a2]

    def to_dict(self):
        """Return typical info as a dict"""
        return {
            "kind": "arc",
            "cx": self.cx,
            "cy": self.cy,
            "r1": self.r1,
            "r2": self.r2,
            "a1": self.a1,
            "a2": self.a2,
        }

    def bounding_box(self):
        # get the 4 'corners' points
        pts = [self.p1, self.p2, self.p3, self.p4]

        # add extra points (intersection with X and Y axes)
        # force positive angles and a1 > a2 (so a2 could be greater than 360 and up to 540)
        a1 = self.a1 % 360
        a2 = self.a2 % 360
        if a2 < a1:
            a2 += 360
        for theta in range(0, 360 * 2, 90):
            if a1 < theta < a2:
                a = numpy.deg2rad(theta)
                px = self.r2 * numpy.cos(a) + self.cx
                py = self.r2 * numpy.sin(a) + self.cy
                pts.append([px, py])

        pts = numpy.array(pts)
        xmini = pts[:, 0].min()
        xmaxi = pts[:, 0].max()
        ymini = pts[:, 1].min()
        ymaxi = pts[:, 1].max()

        return [[xmini, ymini], [xmaxi, ymaxi]]


class ROI_PROFILE_MODES(str, enum.Enum):
    horizontal = "LINES_SUM"
    vertical = "COLUMN_SUM"


_PMODE_ALIASES = {
    "horizontal": ROI_PROFILE_MODES.horizontal,
    "h": ROI_PROFILE_MODES.horizontal,
    0: ROI_PROFILE_MODES.horizontal,
    "vertical": ROI_PROFILE_MODES.vertical,
    "v": ROI_PROFILE_MODES.vertical,
    1: ROI_PROFILE_MODES.vertical,
}


class RoiProfile(Roi):
    def __init__(self, x, y, width, height, mode="horizontal", name=None):

        self.mode = mode

        super().__init__(x, y, width, height, name)

    @property
    def mode_vector(self):
        """Returns the profile mode as a unitary vector"""
        return self._mode_vector

    @property
    def mode(self):
        return self._mode.name

    @mode.setter
    def mode(self, mode):
        if mode not in _PMODE_ALIASES.keys():
            raise ValueError(f"the mode should be in {_PMODE_ALIASES.keys()}")

        self._mode = _PMODE_ALIASES[mode]

        if self._mode is ROI_PROFILE_MODES.horizontal:
            self._mode_vector = (1, 0)
        else:
            self._mode_vector = (0, 1)

    def __repr__(self):
        return "<%s,%s> <%s x %s> <%s>" % (
            self.x,
            self.y,
            self.width,
            self.height,
            self.mode,
        )

    def __eq__(self, other):
        if other.__class__ != self.__class__:
            return False
        ans = self.x == other.x and self.y == other.y
        ans = ans and self.width == other.width and self.height == other.height
        ans = ans and self.name == other.name
        ans = ans and self.mode == other.mode
        return ans

    def to_dict(self):
        return {
            "kind": "profile",
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "mode": self.mode,
        }


def dict_to_roi(dico: dict) -> _BaseRoi:
    """Convert a dictionary generated by `_BaseRoi.to_dict()` into an object ROI

    Argument:
        dico: Description of a ROI as a flat dictionary

    Raises:
        ValueError: If the dictionary do not represent a ROI
    """
    roiClasses = {"rect": Roi, "arc": ArcRoi, "profile": RoiProfile}

    # Do not edit the base object
    dico = dict(dico)

    try:
        kind = dico.pop("kind")
    except KeyError:
        raise ValueError("ROI kind is expected")

    roiClass = roiClasses.get(kind)
    if roiClass is None:
        raise ValueError("Unknown ROI kind '%s'" % kind)

    try:
        roi = roiClass(**dico)
    except Exception as e:
        raise ValueError("Wrong ROI dictionary") from e
    return roi
