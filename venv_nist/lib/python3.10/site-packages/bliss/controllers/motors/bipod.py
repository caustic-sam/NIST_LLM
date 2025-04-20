# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Bipod calc controller: Motor Calc controller for 2 legs tables.

doc: https://bliss.gitlab-pages.esrf.fr/bliss/master/config_bipod.html
"""

import numpy as np
import tabulate

from bliss.common.logtools import log_debug
from bliss.controllers.motor import CalcController
from bliss.physics.units import ur
from bliss.shell.standard import wm
from bliss.config import settings


class bipod(CalcController):
    """
    Motor Calc controller for 2 legs tables.
    """

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
            for key in ["ax", "bx"]:
                self._centers[center_id][key] = center_param.get(key, 0)

            ax = self._centers[center_id]["ax"]
            bx = self._centers[center_id]["bx"]
            log_debug(self, f"center_id: {center_id}")
            log_debug(self, f"    ax = {ax}")
            log_debug(self, f"    bx = {bx}")

        self._selected_center = settings.SimpleSetting(
            f"tripod_{self.name}_selected_center",
            default_value=default_center,
        )

    def initialize(self):
        CalcController.initialize(self)

        # get all motor units
        self.lega_unit = self._tagged["lega"][0].unit
        if self.lega_unit is None:
            self.lega_unit = "m"

        self.legb_unit = self._tagged["legb"][0].unit
        if self.legb_unit is None:
            self.legb_unit = "m"

        self.tz_unit = self._tagged["tz"][0].unit
        if self.tz_unit is None:
            self.tz_unit = "m"

        self.ry_unit = self._tagged["ry"][0].unit
        if self.ry_unit is None:
            self.ry_unit = "rad"

    def __info__(self):
        """
        Return info string for inline doc.
        """
        mystr = "Type           : bipod\n\n"
        mystr += f"Selected center: {self.center}\n"
        mystr += f"    ax = {self.ax}\n"
        mystr += f"    bx = {self.bx}\n\n"
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

    def wa(self):
        mot_list = self.pseudos + self.reals
        wm(*mot_list)

    @property
    def center(self):
        return self._selected_center.get()

    @center.setter
    def center(self, center_id):
        if center_id in self._centers.keys():
            self._selected_center.set(center_id)
            self.sync_hard()

    @property
    def ax(self):
        return self._centers[self.center]["ax"]

    @property
    def bx(self):
        return self._centers[self.center]["bx"]

    def calc_from_real(self, real_dict):

        log_debug(self, "calc_from_real()")

        lega = (real_dict["lega"] * ur.parse_units(self.lega_unit)).to("m").magnitude
        legb = (real_dict["legb"] * ur.parse_units(self.legb_unit)).to("m").magnitude

        if not isinstance(lega, np.ndarray):
            lega = np.array([lega], dtype=float)
            legb = np.array([legb], dtype=float)

        ry = np.arctan((lega - legb) / (self.bx - self.ax))
        tz = lega + self.ax * np.tan(ry)

        ry = (ry * ur.rad).to(self.ry_unit).magnitude
        tz = (tz * ur.m).to(self.tz_unit).magnitude

        if len(lega) == 1:
            return {"tz": tz[0], "ry": ry[0]}
        return {"tz": tz, "ry": ry}

    def calc_to_real(self, calc_dict):

        log_debug(self, "calc_to_real()")

        tz = (calc_dict["tz"] * ur.parse_units(self.tz_unit)).to("m").magnitude
        ry = (calc_dict["ry"] * ur.parse_units(self.ry_unit)).to("rad").magnitude

        if not isinstance(tz, np.ndarray):
            tz = np.array([tz], dtype=float)
            ry = np.array([ry], dtype=float)

        lega = tz - self.ax * np.tan(ry)
        legb = tz - self.bx * np.tan(ry)

        lega = (lega * ur.m).to(self.lega_unit).magnitude
        legb = (legb * ur.m).to(self.legb_unit).magnitude

        if len(tz) == 1:
            return {"lega": lega[0], "legb": legb[0]}
        return {"lega": lega, "legb": legb}
