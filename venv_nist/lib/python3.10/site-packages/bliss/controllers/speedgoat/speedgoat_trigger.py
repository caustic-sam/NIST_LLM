# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import tabulate

"""
SPEEDGOAT triggers
"""


class SpeedgoatHdwTriggerController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._triggers = None
        self._load()

    def __info__(self):
        if self._triggers is None:
            return "    No trigger in the model"
        lines = [["    ", "Name", "Unique Name"]]
        for trigger in self._triggers.values():
            lines.append(["    ", trigger.name, trigger._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._triggers is None or force:
            triggers = self._speedgoat._get_all_objects_from_key("bliss_trigger")
            if len(triggers) > 0:
                self._triggers = {}
                for trigger in triggers:
                    sp_trigger = SpeedgoatHdwTrigger(self._speedgoat, trigger)
                    setattr(self, sp_trigger.name, sp_trigger)
                    self._triggers[sp_trigger.name] = sp_trigger
        return self._triggers


class SpeedgoatHdwTrigger:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(
            f"{self._unique_name}/bliss_trigger/String"
        )

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        lines.append(["", ""])
        lines.append(["Number of trigs", self.trig_number])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _tree(self):
        self._speedgoat.parameter._load()["param_tree"].subtree(
            self._unique_name
        ).show()

    @property
    def trig_number(self):
        return int(self._speedgoat.signal.get(f"{self._unique_name}/trig_number"))

    def reset_trig_number(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/start_trigger/Bias", 0)
        reset_trigger = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/reset_trig_number/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/reset_trig_number/Bias", reset_trigger + 1
        )

    def trig(self):
        bias = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/start_trigger/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/start_trigger/Bias", bias + 1
        )
