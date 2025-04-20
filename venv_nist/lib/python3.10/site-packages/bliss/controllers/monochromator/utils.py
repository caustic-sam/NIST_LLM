# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
???
"""

import tabulate
import functools
import enum

from bliss.config import settings
from bliss.common.utils import BOLD, BLUE, ORANGE
from bliss.controllers.motor import CalcController


def motor_esync(axis):
    axis.hw_state
    ch = axis.address
    axis.controller.raw_write(f"{ch}:esync")
    axis.controller.raw_write(f"{ch}:power on")
    axis.sync_hard()


def w_colored(*motors):
    print("")
    lines = []
    line1 = [BOLD("    Name")]
    line2 = [BOLD("    Pos.")]
    for mot in motors:
        if isinstance(mot.controller, CalcController):
            line1.append(f"{ORANGE(mot.name)}({mot.unit})")
        else:
            line1.append(f"{BLUE(mot.name)}({mot.unit})")
        line2.append(f"{mot.position:.3f}")
    lines.append(line1)
    lines.append(line2)
    mystr = tabulate.tabulate(lines, tablefmt="plain")
    print(mystr)


def motor_info(mot_list):
    """
    Print position  dial  offset  lim-  lim+  state of a list of motors.
    A compact variation on wm().

    Example:
    TEST_SESSION [12]: print(motor_info([bad, roby, custom_axis]))
                 position  dial  offset  lim-  lim+  state
    bad          0         0.0   0.0     -inf  inf   ['READY']
    roby         3.3       3.3   0.0     -inf  inf   ['READY']
    custom_axis  0         0.0   0.0     -inf  inf   ['READY']
    """
    headers_list = ["", "position", "dial", "offset", "lim-", "lim+", "state"]
    table = []

    for mot in mot_list:
        mot_state = mot.state.current_states_names
        mot_info = (
            mot.name,
            f"{mot.position:g}",
            f"{mot.dial:g}",
            f"{mot.offset:g}",
            f"{mot.low_limit:g}",
            f"{mot.high_limit:g}",
            mot_state,
        )
        table.append(mot_info)

    info_str = tabulate(tuple(table), tablefmt="plain", headers=headers_list)

    return info_str


class EmptyObject:
    pass


class UserList:
    def __init__(self, name, value_list, default_value=None):
        self._value_dict = {}
        if isinstance(value_list, list):
            for value in value_list:
                val = self._get_val(value)
                self._value_dict[val] = value
                setattr(self, val, functools.partial(self._set, val))
        elif isinstance(value_list, enum.Enum):
            for name in value_list:
                setattr(self, name.name, functools.partial(self._set, name.name))
                self._value_dict[name.name] = name.name
        else:
            raise RuntimeError(
                f"UserList ({name}): value_list parameter must be of type list or enum"
            )
        self._value_setting = settings.SimpleSetting(
            f"UserList_{name}_value", default_value=default_value
        )

    def __info__(self):
        return self._get_val(self._value)

    def _set(self, value):
        self._value = value

    @property
    def _value(self):
        if self._value_setting.get() is None:
            return "None"
        return self._value_dict[self._value_setting.get()]

    @_value.setter
    def _value(self, value):
        val = self._get_val(value)
        self._value_setting.set(val)

    def _get_val(self, value):
        if isinstance(value, str):
            return value
        if hasattr(value, "name"):
            return value.name
        if hasattr(value, "_name"):
            return value._name
        return None
