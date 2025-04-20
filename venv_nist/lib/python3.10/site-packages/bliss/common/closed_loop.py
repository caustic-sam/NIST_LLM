# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import enum
from functools import partial

from bliss.config.beacon_object import BeaconObject, EnumProperty


class ClosedLoopState(enum.Enum):
    UNDEFINED = enum.auto()
    UNKNOWN = enum.auto()
    ON = enum.auto()
    OFF = enum.auto()


def fget_gen(key):
    def f(key, self):
        return self.axis.controller.get_closed_loop_param(self.axis, key)

    fget = partial(f, key)
    fget.__name__ = key
    return fget


def fset_gen(key):
    def f(key, self, value):
        if self._setters_on:
            return self.axis.controller.set_closed_loop_param(self.axis, key, value)

    fset = partial(f, key)
    fset.__name__ = key
    return fset


class ClosedLoop(BeaconObject):
    """
    config example:
    - name: m1
      steps_per_unit: 1000
      velocity: 50
      acceleration: 1
      encoder: $m1enc
      closed_loop:
          state: on
          kp: 1
          ki: 2
          kd: 3
          settling_window: 0.1
          settling_time: 3
    """

    def __new__(cls, axis):
        """Make a class copy per instance to allow closed loop objects to own different properties"""
        cls = type(cls.__name__, (cls,), {})
        return object.__new__(cls)

    def __init__(self, axis):
        self._axis = axis
        name = f"{axis.name}:closed_loop"
        config = axis.config.config_dict
        if isinstance(config, dict):
            super().__init__(config.get("closed_loop"), name=name)
        else:
            super().__init__(config, name=name, path=["closed_loop"])

        setattr(
            self.__class__,
            "_state",
            EnumProperty("state", ClosedLoopState, must_be_in_config=True),
        )

        self._setters_on = False
        self._init_properties()

    def _init_properties(self):
        """Instantiate properties depending on the controller requirements"""
        reqs = self.axis.controller.get_closed_loop_requirements()
        for key in reqs:
            if hasattr(self, key):
                raise Exception(
                    f"Cannot create closed loop property '{key}', name already exists"
                )
            setattr(
                self.__class__,
                key,
                BeaconObject.property(
                    fget=fget_gen(key), fset=fset_gen(key), must_be_in_config=True
                ),
            )

    def __info__(self):
        info_str = "CLOSED LOOP:\n"
        info_str += f"     state: {self.state.name}\n"
        for key in self.axis.controller.get_closed_loop_requirements():
            info_str += f"     {key}: {getattr(self, key)}\n"
        return info_str

    @property
    def axis(self):
        return self._axis

    @property
    def state(self):
        return self._state

    def _activate(self, onoff):
        try:
            self.axis.controller.activate_closed_loop(self.axis, onoff)
        except Exception as e:
            self._state = self.axis.controller.get_closed_loop_state(self.axis)
            raise RuntimeError(
                f"Failed to turn {self.name} {'ON' if onoff else 'OFF'}"
            ) from e
        else:
            self._state = ClosedLoopState.ON if onoff else ClosedLoopState.OFF

    def on(self):
        self._activate(True)

    def off(self):
        self._activate(False)

    def _activate_setters(self):
        self._setters_on = True

    def sync_hard(self):
        self._state = self.axis.controller.get_closed_loop_state(self.axis)
        setters_state = self._setters_on
        self._setters_on = False
        for key in self.axis.controller.get_closed_loop_requirements():
            setattr(
                self, key, self.axis.controller.get_closed_loop_param(self.axis, key)
            )
        self._setters_on = setters_state
