# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import tabulate

"""
SPEEDGOAT Lookup Tables
"""


class SpeedgoatHdwLutController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._luts = None
        self._load()

    def __info__(self):
        if self._luts is None:
            return "    No LUT in the model"

        lines = [["    ", "Name", "Unique Name"]]
        for lut in self._luts.values():
            lines.append(["    ", lut.name, lut._unique_name])

        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._luts is None or force:
            luts = self._speedgoat._get_all_objects_from_key("bliss_lut")
            if len(luts) > 0:
                self._luts = {}
                for _lut in luts:
                    sp_lut = SpeedgoatHdwLut(self._speedgoat, _lut)
                    setattr(self, sp_lut.name, sp_lut)
                    self._luts[sp_lut.name] = sp_lut

        return self._luts


class SpeedgoatHdwLut:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(f"{unique_name}/bliss_lut/String")

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        lines.append(["Enabled", self.enabled])
        y_raw = self.y_raw
        y_raw_str = f"[{y_raw[0]}, {y_raw[1]}, ..., {y_raw[-2]}, {y_raw[-1]}]"
        x_raw = self.x_raw
        x_raw_str = f"[{x_raw[0]}, {x_raw[1]}, ..., {x_raw[-2]}, {x_raw[-1]}]"
        lines.append(["X raw", x_raw_str])
        lines.append(["Y raw", y_raw_str])
        lines.append(["", ""])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _tree(self):
        self._speedgoat.parameter._load()["param_tree"].subtree(
            self._unique_name
        ).show()

    def enable(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/enable", 1)

    def disable(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/enable", 0)

    @property
    def length(self):
        return len(self.x_raw)

    @property
    def enabled(self):
        return bool(self._speedgoat.parameter.get(f"{self._unique_name}/enable"))

    @property
    def y_raw(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/y_raw")

    @y_raw.setter
    def y_raw(self, value):
        self._speedgoat.parameter.set(f"{self._unique_name}/y_raw", value)

    @property
    def x_raw(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/x_raw")

    @x_raw.setter
    def x_raw(self, value):
        self._speedgoat.parameter.set(f"{self._unique_name}/x_raw", value)

    @property
    def y_input(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/y_input")

    @property
    def y_output(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/y_output")

    @property
    def x_data(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/x_data")

    @property
    def y_data(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/y_data")
