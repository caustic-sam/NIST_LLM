# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
4-motors Q-Sys support table for transfocators

(Used at ESRF: ID22, ID31, ID15A, ID28, ...)

Real motor roles:
* *dh*: downstream hall
* *dr*: downstream ring
* *ur*: upstream ring
* *uh*: upstream hall

Calc. motor roles:
* ty*: alias for calculated y translation
* *tz*: alias for calculated z translation
* *thetay*: alias for calculated y rotation
* *thetaz*: alias for calculated z rotation

Configuration parameters:
* *d1*: half distance in x direction (in mm) between the two moving blocks.
* *d3*: distance in z direction (in mm) between pivot plane and the top plate.

POI : point of interest (rotation center)

.. note:: We are using orientation conventions used on Q-Sys document.

::

    <---X---Z
            |
            |
            Y
            |
            |
            V

       M2=DR                M3=UR
                POI
                   X           <------------------------- BEAM

       M1=DH       |        M4=UH
        |          |
        |<---D1--->|


"""

from bliss.controllers.motor import CalcController
import numpy


class trans4(CalcController):
    def __init__(self, *args, **kwargs):
        CalcController.__init__(self, *args, **kwargs)

    def calc_from_real(self, positions_dict):
        """
        Returns calculated/virtual motor positions (as a dictionary) given real ones.

        Units
            Distances d1 and d2, real motors positions and calculated/virtual z and y
            motor positions are in millimeters.
            Calculated/virtual rotation around y and z axis in milliradians
        """
        d1 = self.config.get("d1", float)
        d2 = self.config.get("d2", float)
        alpha = numpy.arctan(d2 / d1)
        cos2_alpha = numpy.cos(alpha) ** 2

        dh = positions_dict["dh"]
        dr = positions_dict["dr"]
        ur = positions_dict["ur"]
        uh = positions_dict["uh"]

        p1y = (dr - dh) / 2.0
        p1z = (dr + dh) / 2.0
        p2y = (ur - uh) / 2.0
        p2z = (ur + uh) / 2.0

        ty = (p1y + p2y * cos2_alpha) / (1 + cos2_alpha)
        thetaz = -numpy.arctan((p1y - p2y) / d1 * (cos2_alpha / (1 + cos2_alpha)))
        tz = (p2z + p1z) / 2.0
        thetay = numpy.arctan((p2z - p1z) / (2 * d1))

        thetaz *= 1000.0
        thetay *= 1000.0

        return dict(thetaz=thetaz, thetay=thetay, ty=ty, tz=tz)

    def calc_to_real(self, positions_dict):
        """
        Returns real motors positions (as a dictionary) given virtual ones.
        Units:
        Distances d1 and d2, real motors positions and calculated/virtual z and y
        motor positions are in millimeters.
        Rotation around Y-axis(thetay) and Z-axis(thetaz) are in milliradians.
        """
        d1 = self.config.get("d1", float)
        d2 = self.config.get("d2", float)
        alpha = numpy.arctan(d2 / d1)
        cos2_alpha = numpy.cos(alpha) ** 2

        ty = positions_dict["ty"]
        tz = positions_dict["tz"]
        thetay = positions_dict["thetay"] / 1000.0
        thetaz = -positions_dict["thetaz"] / 1000.0

        p1y = ty + d1 * numpy.tan(thetaz)
        p2y = ty - d1 * numpy.tan(thetaz) / cos2_alpha
        p1z = tz - d1 * numpy.tan(thetay)
        p2z = tz + d1 * numpy.tan(thetay)

        dh = p1z - p1y
        dr = p1z + p1y
        ur = p2z + p2y
        uh = p2z - p2y

        # print _real_dict
        return dict(dh=dh, dr=dr, uh=uh, ur=ur)
