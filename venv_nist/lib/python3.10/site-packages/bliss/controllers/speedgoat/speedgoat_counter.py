# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
SPEEDGOAT COUNTERS
"""

import numpy as np
import tabulate
from bliss.shell.formatters.table import IncrementalTable


class SpeedgoatHdwCounterController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._counters = None
        self._load()

    def __info__(self):
        """Display list of all counters"""
        if self._counters is None:
            return "    No Counter in the model"
        lines = [["Name", "Unit", "Description"]]
        tab = IncrementalTable(lines, col_sep=" | ", flag="", lmargin="  ", align="<")
        for counter in self._counters.values():
            tab.add_line([counter.name, counter.unit, counter.description])
        tab.resize(10, 100)
        tab.add_separator("-", line_index=1)
        mystr = "\n" + str(tab)
        return mystr

    def setResponseTime(self, response_time):
        """Tune the response time (time it takes to go to 63% of final value) for ALL filtered counters"""
        for filtered_counter in self._filtered_counters.values():
            filtered_counter.response_time = response_time

    def setFilteredFrequency(self, fc):
        """Tune the cut-off frequency time for ALL filtered counters."""
        for filtered_counter in self._filtered_counters.values():
            filtered_counter.fc = fc

    def _load(self, force=False):
        """Automatically discover counters in Speedgoat model."""
        if self._counters is None or force:
            counter_tree = self._speedgoat.parameter._tree.subtree("counters")
            self._counters = {}
            self._filtered_counters = {}
            for node in counter_tree.children("counters"):
                # Check whether the counter il filtered or not
                if node.tag[-8:] == "filtered":
                    sp_counter = SpeedgoatHdwFilteredCounter(self._speedgoat, node.tag)
                    self._filtered_counters[node.tag] = sp_counter
                elif node.tag[-3:] == "std":
                    sp_counter = SpeedgoatHdwFilteredCounter(self._speedgoat, node.tag)
                    self._filtered_counters[node.tag] = sp_counter
                else:
                    sp_counter = SpeedgoatHdwCounter(self._speedgoat, node.tag)
                setattr(self, node.tag, sp_counter)
                self._counters[node.tag] = sp_counter
        return self._counters


class SpeedgoatHdwCounter:
    """Speedgoat Counter - Has name, description, unit and value"""

    def __init__(self, speedgoat, counter_name):
        self._speedgoat = speedgoat
        self.name = counter_name

        # Store the base name for easy access to some paramters
        if self.name[-8:] == "filtered":
            self._base_name = self.name[:-9]
        elif self.name[-3:] == "std":
            self._base_name = self.name[:-4]

        self.description = self._speedgoat.parameter.get(
            f"counters/{counter_name}/counter_description/String"
        )

        self.unit = self._speedgoat.parameter.get(
            f"counters/{counter_name}/counter_unit/String"
        )

        self._index = int(
            self._speedgoat.parameter.get(f"counters/{counter_name}/counter_index")
        )

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Description", self.description])
        lines.append(["Unit", self.unit])
        lines.append(["Index", self._index])
        lines.append(["", ""])
        lines.append(["Counter Value", self.value])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _tree(self):
        param = f"counters/{self.name}"
        self._speedgoat.parameter._cache["param_tree"].subtree(param).show()

    @property
    def value(self):
        signal = f"counters/{self.name}/counter_value"
        return float(self._speedgoat.signal.get(signal))


class SpeedgoatHdwFilteredCounter(SpeedgoatHdwCounter):
    """Filtered Counters - A low pass filter is integrated for each filtered counter.
    The cut-off frequency of the low pass filter can be individually tuned for each filter.
    """

    def __init__(self, speedgoat, counter_name):
        super().__init__(speedgoat, counter_name)

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Cut-off frequency:", f"{self.fc:.1f} Hz"])
        lines.append(["Response time (63%):", f"{self.response_time:.3f} s"])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def response_time(self):
        return self._speedgoat.parameter.get(
            f"filtered_counters/{self._base_name}/filter/tau"
        )

    @response_time.setter
    def response_time(self, tau):
        """The counters are filtered with 1st order low pass filters.
        tau the rise time (63%) of the filter."""
        self._speedgoat.parameter.set(
            f"filtered_counters/{self._base_name}/filter/tau", tau
        )

    @property
    def fc(self):
        return 1 / self.response_time / 2 / np.pi

    @fc.setter
    def fc(self, fc):
        """The counters are filtered with 1st order low pass filters.
        fc is the cut-off frequency specified in [Hz]"""
        self.response_time = 1 / 2 / np.pi / fc
