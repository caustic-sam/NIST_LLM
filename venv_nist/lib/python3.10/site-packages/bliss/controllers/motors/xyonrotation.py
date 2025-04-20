# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Virtual X and Y axis which stay on top of a rotation
"""

import numpy
from bliss.controllers.motor import CalcController
from bliss.common.utils import object_method


class XYOnRotation(CalcController):
    """
    Virtual X and Y axis on top of a rotation

    .. code-block:: yaml

        class: XYOnRotation

        # if rotation is inverted
        # optional, default is False
        inverted: False

        # if rotation angle is in radian
        # optional, default is False == degree
        # deprecated, prefer setting unit "deg"/"rad" to the rotation axis
        radian: False           # optional

        # Use dial instead of user position for the rotation
        # optional, default is False
        use_dial_rot: True

        axes:
            - name: $rot
              tags: real rot
            - name: $px
              tags: real rx     # real X
            - name: $py
              tags: real ry     # real Y
            - name: sampx
              tags: x
            - name: sampy
              tags: y
    """

    def _add_input_axis(self, axis, axis_tags):
        """Decide if an input axis should be added to the reals or the params axes lists.
        Choice is made from the axis_tags list knowledge.
        In this class, the choice is made looking for the specific 'rot' tag in tags list
        to identify a parametric axis, else it is considered as a real.
        """

        if "rot" in axis_tags:
            self.params.append(axis)
        elif "rx" in axis_tags or "ry" in axis_tags:
            self.reals.append(axis)
        else:
            raise ValueError(f"Unknown tags {axis_tags} for axis {axis.name}")

    def _check_units(self):

        unit = self._tagged["rot"][0].unit
        if unit == "rad":
            # Sanity check if radian property was defined
            if self.__radian is False:
                raise ValueError(
                    f"Discrepency between radian property ({self.__radian}) and rotation unit ({unit}) "
                )
            self.__radian = True  # __radian could be None

        elif unit == "deg":
            # Sanity check if radian property was defined
            if self.__radian is True:
                raise ValueError(
                    f"Discrepency between radian property ({self.__radian}) and rotation unit ({unit}) "
                )
            self.__radian = False  # __radian could be None

        elif self.__radian is None:
            self.__radian = False

        rx = self._tagged["rx"][0]
        ry = self._tagged["ry"][0]
        assert (
            rx.unit == ry.unit
        ), f"Real motors must use the same units (found '{rx.name}' with '{rx.unit}' and '{ry.name}' with '{ry.unit}')"

        unit = rx.unit
        if unit is not None:
            cx = self._tagged["x"][0]
            cy = self._tagged["y"][0]

            if cx.unit is None:
                cx._unit = unit
            else:
                assert (
                    cx.unit == unit
                ), f"Calc motors must use the same unit as real motors (found '{cx.name}' with '{cx.unit}' and '{rx.name}' with '{unit}')"

            if cy.unit is None:
                cy._unit = unit
            else:
                assert (
                    cy.unit == unit
                ), f"Calc motors must use the same unit as real motors (found '{cy.name}' with '{cy.unit}' and '{rx.name}' with '{unit}')"

    def _get_rot_rad(self, rot):
        if self.__radian:
            rot_rad = rot
        else:
            rot_rad = numpy.deg2rad(rot)
        rot_rad *= self.__inverted
        return rot_rad

    def initialize(self):
        # add rotation offset in motor settings
        self.axis_settings.add("rotation_offset", float)

        CalcController.initialize(self)

        self.__inverted = -1 if self.config.get("inverted", default=False) else 1
        self.__use_dial_rot = self.config.get("use_dial_rot", bool, default=False)
        self.__radian = self.config.get("radian", bool)
        self._check_units()

    def calc_from_real(self, user_positions):
        rx = user_positions["rx"]
        ry = user_positions["ry"]
        rot = user_positions["rot"]

        if self.__use_dial_rot:
            rot_axis = self._tagged["rot"][0]
            rot = rot_axis.user2dial(rot)

        rot += self._tagged["x"][0].rotation_offset()

        rot_rad = self._get_rot_rad(rot)
        return {
            "x": rx * numpy.cos(rot_rad) - ry * numpy.sin(rot_rad),
            "y": rx * numpy.sin(rot_rad) + ry * numpy.cos(rot_rad),
        }

    def calc_to_real(self, dial_positions):
        x = dial_positions["x"]
        y = dial_positions["y"]
        rot = dial_positions["rot"]

        if not self.__use_dial_rot:
            rot_axis = self._tagged["rot"][0]
            rot = rot_axis.dial2user(rot)

        rot += self._tagged["x"][0].rotation_offset()

        rot_rad = self._get_rot_rad(rot)
        return {
            "rx": x * numpy.cos(rot_rad) + y * numpy.sin(rot_rad),
            "ry": -x * numpy.sin(rot_rad) + y * numpy.cos(rot_rad),
        }

    @object_method(types_info=("None", "float"))
    def rotation_offset(self, axis, offset=None):
        """
        get/set rotation offset between rotation motor and
        virtual axes
        """
        if offset is None:
            rotation_offset = axis.settings.get("rotation_offset")
            return rotation_offset if rotation_offset else 0
        else:
            for axis in self.axes.values():
                axis.settings.set("rotation_offset", offset)
