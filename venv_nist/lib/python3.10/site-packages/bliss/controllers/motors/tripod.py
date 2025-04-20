# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Convention for kinematics calculations:

    1 - We are using a direct coordiante system (x,y,z) as follow

                    ^
                    | Z axis
                    |
        X axis      |
          <----------
                   /
                  /
                 / Y axis
                /
                v

    2 - The rotation are name rotx, roty, rotz around respectively the
        X, Y and Z axis with counter-clockwize positive value

    3 - all parameters relative to legs are following the same convention:
            - coordinates of "lega" are named: "ax", "ay"
            - coordinates of "legb" are named: "bx", "by"
            - ...

    4 - all parameters are given in meters

    5 - The calculation takes into account the "unit" defined for each
        real or calculated motors.
        if the "unit" field is missing, the universal units are used:
        meters (m) for distance, radians (rad) for angles
"""

import numpy as np
import tabulate

from bliss.controllers.motor import CalcController
from bliss.physics.units import ur
from bliss.config import settings


class tripod(CalcController):
    def __init__(self, *args, **kwargs):
        CalcController.__init__(self, *args, **kwargs)

        center_params = self.config.get("centers", None)
        if center_params is None:
            raise RuntimeError("No Center defined")

        self._centers = {}
        for center_param in center_params:
            center_id = center_param.get("center_id", None)
            default_center = center_id
            if center_id is None:
                raise RuntimeError("No center_id defined")
            self._centers[center_id] = {}
            for key in ["ax", "ay", "bx", "by", "cx", "cy"]:
                self._centers[center_id][key] = center_param.get(key, 0)

        self._selected_center = settings.SimpleSetting(
            f"tripod_{self.name}_selected_center",
            default_value=default_center,
        )

        self._calculate_matrix(self._selected_center.get())

    def _calculate_matrix(self, center_id):
        """
        ???
        """
        # Get (x,y) actuator coordinates from configuration file
        self.ax = self._centers[center_id]["ax"]
        self.ay = self._centers[center_id]["ay"]
        self.bx = self._centers[center_id]["bx"]
        self.by = self._centers[center_id]["by"]
        self.cx = self._centers[center_id]["cx"]
        self.cy = self._centers[center_id]["cy"]

        # (Jacobian) Matrix used to go from "struts motion [Da,Db,Dc]" to "object motion [Dz,Rx,Ry]"
        self.Ja = np.array(
            [
                [1.0, self.ay, -self.ax],
                [1.0, self.by, -self.bx],
                [1.0, self.cy, -self.cx],
            ]
        )

        # (Inverse Jacobian) Matrix used to go from "object motion [Dz,Rx,Ry]" to "struts motion [Da,Db,Dc]"
        self.Ja_inv = np.linalg.inv(self.Ja)

    @property
    def center(self):
        return self._selected_center.get()

    @center.setter
    def center(self, center_id):
        if center_id in self._centers.keys():

            self._selected_center.set(center_id)

            self._calculate_matrix(center_id)

            self.sync_hard()

    def initialize(self):
        CalcController.initialize(self)

        # Get all real motors' unit
        self.lega_unit = self._tagged["lega"][0].unit
        self.legb_unit = self._tagged["legb"][0].unit
        self.legc_unit = self._tagged["legc"][0].unit

        # Get pseudo motors' unit
        self.tz_unit = self._tagged["tz"][0].unit
        self.rx_unit = self._tagged["rx"][0].unit
        self.ry_unit = self._tagged["ry"][0].unit

    def __info__(self):
        mystr = "Type: Tripod\n"
        mystr += f"Name: {self._selected_center}\n\n"

        mystr += f"ax[{self.ax}] ay[{self.ay}]\n"
        mystr += f"bx[{self.bx}] by[{self.by}]\n"
        mystr += f"cx[{self.cx}] cy[{self.cy}]\n\n"

        title = []
        user = []
        for axis in self.pseudos:
            title.append(f"{axis.name}[{axis.unit}]")
            user.append(f"{axis.position:.4f}")
        mystr += tabulate.tabulate([title, user], tablefmt="plain")
        mystr += "\n\n"
        title = []
        user = []
        for axis in self.reals:
            title.append(f"{axis.name}[{axis.unit}]")
            user.append(f"{axis.position:.4f}")
        mystr += tabulate.tabulate([title, user], tablefmt="plain")
        return mystr

    def calc_from_real(self, real_dict):
        """Computes the calculated motor positions [Dz, Rx, Ry] from the real motor positions [Da, Db, Dc]"""

        # Get real positions in [m] and make sure they are numpy arrays
        lega = np.array(
            (real_dict["lega"] * ur.parse_units(self.lega_unit)).to("m").magnitude,
            dtype=float,
        )
        legb = np.array(
            (real_dict["legb"] * ur.parse_units(self.legb_unit)).to("m").magnitude,
            dtype=float,
        )
        legc = np.array(
            (real_dict["legc"] * ur.parse_units(self.legc_unit)).to("m").magnitude,
            dtype=float,
        )

        # Compute corresponding real motor positions
        calc_pos = self.Ja_inv @ np.array([lega, legb, legc])

        # Return real motor positions
        return {
            "tz": (calc_pos[0] * ur.m).to(self.tz_unit).magnitude,
            "rx": (calc_pos[1] * ur.rad).to(self.rx_unit).magnitude,
            "ry": (calc_pos[2] * ur.rad).to(self.ry_unit).magnitude,
        }

    def calc_to_real(self, positions_dict):
        # Get calculated positions in [m/rad/rad] and make sure they are numpy arrays
        tz = np.array(
            (positions_dict["tz"] * ur.parse_units(self.tz_unit)).to("m").magnitude,
            dtype=float,
        )
        rx = np.array(
            (positions_dict["rx"] * ur.parse_units(self.rx_unit)).to("rad").magnitude,
            dtype=float,
        )
        ry = np.array(
            (positions_dict["ry"] * ur.parse_units(self.ry_unit)).to("rad").magnitude,
            dtype=float,
        )

        # Compute corresponding real motor positions
        real_pos = self.Ja @ np.array([tz, rx, ry])

        # Return real motor positions
        return {
            "lega": (real_pos[0] * ur.m).to(self.lega_unit).magnitude,
            "legb": (real_pos[1] * ur.m).to(self.legb_unit).magnitude,
            "legc": (real_pos[2] * ur.m).to(self.legc_unit).magnitude,
        }
