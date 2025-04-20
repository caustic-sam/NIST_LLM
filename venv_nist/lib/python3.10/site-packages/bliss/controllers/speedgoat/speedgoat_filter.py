# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
SPEEDGOAT Signal filters
"""

import tabulate


class SpeedgoatHdwFilterController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._filters = None
        self._load()

    def __info__(self):
        if self._filters is None:
            return "    No Filter in the model"

        lines = [["    ", "Name", "Unique Name"]]
        for _filter in self._filters.values():
            lines.append(["    ", _filter.name, _filter._unique_name])

        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        """Automatically discover all filters present in the model."""
        if self._filters is None or force:
            filters = self._speedgoat._get_all_objects_from_key("bliss_filter")
            if len(filters) > 0:
                self._filters = {}
                for _filter in filters:
                    filter_type = self._speedgoat.parameter.get(
                        f"{_filter}/filter_type/String"
                    )

                    # Initialize all filters depending on their type
                    if filter_type == "first_order_lpf":
                        sp_filter = SpeedgoatFirstOrderLowPassFilter(
                            self._speedgoat, _filter
                        )
                    elif filter_type == "first_order_hpf":
                        sp_filter = SpeedgoatFirstOrderHighPassFilter(
                            self._speedgoat, _filter
                        )
                    elif filter_type == "second_order_lpf":
                        sp_filter = SpeedgoatSecondOrderLowPassFilter(
                            self._speedgoat, _filter
                        )
                    elif filter_type == "second_order_hpf":
                        sp_filter = SpeedgoatSecondOrderHighPassFilter(
                            self._speedgoat, _filter
                        )
                    elif filter_type == "notch":
                        sp_filter = SpeedgoatNotchFilter(self._speedgoat, _filter)
                    elif filter_type == "remove_dc":
                        sp_filter = SpeedgoatRemoveDcFilter(self._speedgoat, _filter)
                    elif filter_type == "moving_average":
                        sp_filter = SpeedgoatMovingAverageFilter(
                            self._speedgoat, _filter
                        )
                    elif filter_type == "general_fir":
                        sp_filter = SpeedgoatGeneralFirFilter(self._speedgoat, _filter)
                    elif filter_type == "general_iir":
                        sp_filter = SpeedgoatGeneralIirFilter(self._speedgoat, _filter)
                    else:
                        sp_filter = SpeedgoatHdwFilter(self._speedgoat, _filter)

                    setattr(self, sp_filter.name, sp_filter)
                    self._filters[sp_filter.name] = sp_filter

        return self._filters


class SpeedgoatHdwFilter:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(f"{unique_name}/bliss_filter/String")

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        lines.append(["Enabled", self.enabled])
        lines.append(["", ""])
        lines.append(["Type", self.type])
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
    def type(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/filter_type/String")

    @property
    def enabled(self):
        return bool(self._speedgoat.parameter.get(f"{self._unique_name}/enable"))

    @property
    def input(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/input")

    @property
    def output(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/output")


class SpeedgoatFirstOrderLowPassFilter(SpeedgoatHdwFilter):
    """First Order Low Pass Filter, only parameter is the cut-off frequency."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)
        self._filter_params = ["wn"]

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Parameters:", ""])
        for param in self._filter_params:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def wn(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_wn")

    @wn.setter
    def wn(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/wn", value)
        except Exception:
            print("wn is dynamically set within the Speedgoat")


class SpeedgoatFirstOrderHighPassFilter(SpeedgoatHdwFilter):
    """First Order High Pass Filter, only parameter is the cut-off frequency."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)
        self._filter_params = ["wn"]

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Parameters:", ""])
        for param in self._filter_params:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def wn(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_wn")

    @wn.setter
    def wn(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/wn", value)
        except Exception:
            print("wn is dynamically set within the Speedgoat")


class SpeedgoatSecondOrderLowPassFilter(SpeedgoatHdwFilter):
    """Second Order Low Pass Filter, parameters are the cut-off frequency and the damping ratio."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)
        self._filter_params = ["wn", "xi"]

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Parameters:", ""])
        for param in self._filter_params:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def xi(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_xi")

    @xi.setter
    def xi(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/xi", value)
        except Exception:
            print("xi is dynamically set within the Speedgoat")

    @property
    def wn(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_wn")

    @wn.setter
    def wn(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/wn", value)
        except Exception:
            print("wn is dynamically set within the Speedgoat")


class SpeedgoatSecondOrderHighPassFilter(SpeedgoatHdwFilter):
    """Second Order High Pass Filter, parameters are the cut-off frequency and the damping ratio."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)
        self._filter_params = ["wn", "xi"]

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Parameters:", ""])
        for param in self._filter_params:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def xi(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_xi")

    @xi.setter
    def xi(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/xi", value)
        except Exception:
            print("xi is dynamically set within the Speedgoat")

    @property
    def wn(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_wn")

    @wn.setter
    def wn(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/wn", value)
        except Exception:
            print("wn is dynamically set within the Speedgoat")


class SpeedgoatNotchFilter(SpeedgoatHdwFilter):
    """Notch Filter, parameters are the frequency of the notch, the gain of the notch
    (that may be <1 or >1) and the damping ratio."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)
        self._filter_params = ["wn", "xi", "gc"]

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Parameters:", ""])
        for param in self._filter_params:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def xi(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_xi")

    @xi.setter
    def xi(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/xi", value)
        except Exception:
            print("xi is dynamically set within the Speedgoat")

    @property
    def gc(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_gc")

    @gc.setter
    def gc(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/gc", value)
        except Exception:
            print("gc is dynamically set within the Speedgoat")

    @property
    def wn(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_wn")

    @wn.setter
    def wn(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/wn", value)
        except Exception:
            print("wn is dynamically set within the Speedgoat")


class SpeedgoatRemoveDcFilter(SpeedgoatHdwFilter):
    """Filter that can be triggered to remove the DC part of the signal."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)

    def __info__(self):
        return super().__info__()

    @property
    def offset(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_offset")

    @offset.setter
    def offset(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/offset", value)
        except Exception:
            print("offset is dynamically set within the Speedgoat")

    def trigger(self):
        trigger_value = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/trigger")
        )
        self._speedgoat.parameter.set(f"{self._unique_name}/trigger", trigger_value + 1)


class SpeedgoatMovingAverageFilter(SpeedgoatHdwFilter):
    """Moving average filter (implemented as a FIR filter).
    Only the average time can be tuned."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)
        self._filter_params = ["avg_time"]

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Parameters:", ""])
        for param in self._filter_params:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def max_avg_time(self):
        return (
            self._speedgoat.signal.get(f"{self._unique_name}/param_nb_taps")
            / self._speedgoat._Fs
        )

    @property
    def avg_time(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_avg_time")

    @avg_time.setter
    def avg_time(self, value):
        max_avg_time = self.max_avg_time
        if value > max_avg_time:
            print("avg_time is above the maximum of {max_avg_time:.3f} s")
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/avg_time", value)
        except Exception:
            print("avg_time is dynamically set within the Speedgoat")


class SpeedgoatGeneralFirFilter(SpeedgoatHdwFilter):
    """Finite Impulse Response (FIR) filter.
    Defined by num_coef."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)
        self._filter_params = ["order", "num_coef"]

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Parameters:", ""])
        for param in self._filter_params:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def order(self):
        return len(self.num_coef) - 1

    @property
    def num_coef(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_num_coef")

    @num_coef.setter
    def num_coef(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/num_coef", value)
        except Exception:
            print("num_coef is dynamically set within the Speedgoat")


class SpeedgoatGeneralIirFilter(SpeedgoatHdwFilter):
    """Infinite Impulse Response (IIR) filter.
    Defined by num_coef and den_coef."""

    def __init__(self, speedgoat, unique_name):
        super().__init__(speedgoat, unique_name)
        self._filter_params = ["order", "num_coef", "den_coef"]

    def __info__(self):
        lines = []
        lines.append(["", ""])
        lines.append(["Parameters:", ""])
        for param in self._filter_params:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return super().__info__() + mystr

    @property
    def order(self):
        return len(self.den_coef) - 1

    @property
    def num_coef(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_num_coef")

    @num_coef.setter
    def num_coef(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/num_coef", value)
        except Exception:
            print("num_coef is dynamically set within the Speedgoat")

    @property
    def den_coef(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/param_den_coef")

    @den_coef.setter
    def den_coef(self, value):
        try:
            self._speedgoat.parameter.set(f"{self._unique_name}/den_coef", value)
        except Exception:
            print("den_coef is dynamically set within the Speedgoat")

    def reset(self):
        reset_states = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/reset/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/reset/Bias", reset_states + 1
        )
