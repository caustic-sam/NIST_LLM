# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

""" BaseShutter, BaseShutterState, ShutterSwitch, Shutter"""

import time
from enum import Enum, unique
from gevent import lock

from bliss.config.beacon_object import BeaconObject
from bliss.config.conductor.client import Lock
from bliss.config.channels import Cache
from bliss.config.settings import HashObjSetting
from bliss.common.logtools import log_warning
from bliss.common import event
from bliss.common.base_shutter import BaseShutter, BaseShutterState
from bliss.common.switch import Switch as _Switch


@unique
class ShutterMode(Enum):
    """Shutter Mode"""

    MANUAL = "Manual mode"
    EXTERNAL = "External trigger mode"
    CONFIGURATION = "Configuration mode"


class _ShutterSwitch(_Switch):
    """Two-states switch with can be created with explicit hardware methods.

    The 2 states are `OPEN` and `CLOSED`.

    Arguments:
        set_open: Callbable to set the hardware to the `OPEN` state
        set_closed: Callbable to set the hardware to the `CLOSED` state
        is_open: Callbable returning True if the hardware is on  the `OPEN` state
    """

    def __init__(self, set_open, set_closed, is_open):
        _Switch.__init__(self, "ShutterSwitch" + str(id(self)), {})

        self._set_open = set_open
        self._set_closed = set_closed
        self._is_open = is_open

    def _states_list(self):
        """Return list of states"""
        return [BaseShutterState.OPEN.name, BaseShutterState.CLOSED.name]

    def _set(self, state):
        """Set state"""
        if state == "OPEN":
            return self._set_open()
        return self._set_closed()

    def _get(self):
        """Get the state"""
        if self._is_open():
            return BaseShutterState.OPEN.name
        return BaseShutterState.CLOSED.name


# Compatibility with BLISS <= 1.10
ShutterSwitch = _ShutterSwitch


class AxisWithExtTriggerShutter(BeaconObject, BaseShutter):
    """
    Abstract generic shutter object.

    This interface should be used for all type of shutter (motor, fast...)

    You may want to link this shutter with an external
    control i.e: wago, musst... in that case you have to put
    in configuration `external_control` with the object reference.

    This external control should be compatible with the `Switch` object
    and have an `OPEN`/`CLOSED` states.

    Some (abstract) function have to be implemented:

    - `_set_mode`
    - `_state`
    - `_open`
    - `_close`
    """

    MANUAL, EXTERNAL, CONFIGURATION = (
        ShutterMode.MANUAL,
        ShutterMode.EXTERNAL,
        ShutterMode.CONFIGURATION,
    )

    def __init__(self, name, config):
        BeaconObject.__init__(self, config)
        self._external_ctrl = config.get("external_control")
        self.__settings = HashObjSetting("shutter:%s" % name)
        self.__state = Cache(
            self,
            "state",
            default_value=BaseShutterState.UNKNOWN,
            callback=self.__state_changed,
        )
        self.__lock = lock.RLock()

    name = BeaconObject.config_getter("name")

    def __state_changed(self, state):
        event.send(self, "state", state)

    def _init(self):
        """
        This method may contains all software initialization
        like communication, internal state...
        """
        pass

    def _initialize_with_setting(self, setting_name=None, setting_value=None):
        if not self._local_initialized:
            self._init()
            if self._external_ctrl is not None:
                # Check if the external control is compatible
                # with a switch object and if it has open/close state
                ext_ctrl = self._external_ctrl
                name = ext_ctrl.name if hasattr(ext_ctrl, "name") else "unknown"
                try:
                    states = ext_ctrl.states_list()
                    ext_ctrl.set
                    ext_ctrl.get
                except AttributeError:
                    raise ValueError(
                        "external_ctrl : {0} is not compatible "
                        "with a switch object".format(name)
                    )
                else:
                    if "OPEN" and "CLOSED" not in states:
                        raise ValueError(
                            "external_ctrl : {0} doesn't"
                            " have 'OPEN' and 'CLOSED' states".format(name)
                        )
            with Lock(self):
                with self.__lock:
                    super()._initialize_with_setting()

    @BeaconObject.property(default=ShutterMode.MANUAL)
    def mode(self):
        """
        shutter mode can be MANUAL,EXTERNAL,CONFIGURATION

        In CONFIGURATION mode, shutter can't be opened/closed.
        **CONFIGURATION** could mean that the shutter is in tuning mode
        i.e: changing open/close position in case of a motor.

        In EXTERNAL mode, the shutter will be controlled
        through the external_control handler.
        If no external control is configured open/close
        won't be authorized.
        """
        pass

    @mode.setter
    def mode(self, value):
        if value not in ShutterMode:
            raise ValueError(
                "Mode can only be: %s" % ",".join(str(x) for x in ShutterMode)
            )
        self._set_mode(value)
        if value in (self.CONFIGURATION, self.EXTERNAL):
            # Can't cache the state if external or configuration
            self.__state.value = BaseShutterState.UNKNOWN

    def _set_mode(self, value):
        raise NotImplementedError

    @property
    def state(self):
        mode = self.mode
        if mode == self.MANUAL and self.__state.value == BaseShutterState.UNKNOWN:
            return_state = self._state()
            self.__state.value = return_state
            return return_state

        if mode == self.CONFIGURATION:
            return BaseShutterState.UNKNOWN
        elif mode == self.EXTERNAL:
            if self.external_control is not None:
                switch_state = self.external_control.get()
                return (
                    BaseShutterState.OPEN
                    if switch_state == "OPEN"
                    else BaseShutterState.CLOSED
                )
            return BaseShutterState.UNKNOWN
        return self.__state.value

    def _state(self):
        raise NotImplementedError

    @property
    def external_control(self):
        """Return the external_control"""
        return self._external_ctrl

    @BeaconObject.lazy_init
    def opening_time(self):
        """
        Return the opening time if available or None
        """
        return self._opening_time()

    def _opening_time(self):
        return self.__settings.get("opening_time")

    @BeaconObject.lazy_init
    def closing_time(self):
        """
        Return the closing time if available or None
        """
        return self._closing_time()

    def _closing_time(self):
        return self.__settings.get("closing_time")

    def measure_open_close_time(self):
        """
        This small procedure will in basic usage do an open and close
        of the shutter to measure the opening and closing time.
        Those timing will be register into the settings.
        returns (opening,closing) time
        """
        previous_mode = self.mode
        try:
            if previous_mode != self.MANUAL:
                self.mode = self.MANUAL
            opening_time, closing_time = self._measure_open_close_time()
            self.__settings["opening_time"] = opening_time
            self.__settings["closing_time"] = closing_time
            return opening_time, closing_time
        finally:
            if previous_mode != self.MANUAL:
                self.mode = previous_mode

    def _measure_open_close_time(self):
        """
        This method can be overloaded if needed.
        Basic timing on. No timeout to wait opening/closing.
        """
        self.close()  # ensure it's closed
        start_time = time.perf_counter()
        self.open()
        opening_time = time.perf_counter() - start_time

        start_time = time.perf_counter()
        self.close()
        closing_time = time.perf_counter() - start_time
        return opening_time, closing_time

    @BeaconObject.lazy_init
    def open(self, timeout: float | None = None):
        """Open the shutter
        Returns:
            (enum): The state of the shutter
        Raises:
            RuntimeError: Cannot open the shutter,
                          no external_control configured.
        """
        if timeout is not None:
            log_warning(self, "Timeout argument is not taken into account")
        mode = self.mode
        if mode == self.EXTERNAL:
            if self._external_ctrl is None:
                raise RuntimeError(
                    "Cannot open the shutter, no external_control configured."
                )
            ret = self._external_ctrl.set("OPEN")
        elif mode != self.MANUAL:
            raise RuntimeError(
                "Cannot open, shutter in %s"
                % self.MODE2STR.get(mode, ("Unknown", "Unknown mode"))[1]
            )
        else:
            ret = self._open()
        self.__state.value = BaseShutterState.OPEN
        return ret

    def _open(self):
        raise NotImplementedError

    @BeaconObject.lazy_init
    def close(self, timeout: float | None = None):
        """Close the shutter
        Returns:
            (enum): The state of the shutter
        Raises:
            RuntimeError: Cannot open the shutter,
                          no external_control configured.
        """
        if timeout is not None:
            log_warning(self, "Timeout argument is not taken into account")
        mode = self.mode
        if mode == self.EXTERNAL:
            if self._external_ctrl is None:
                raise RuntimeError(
                    "Cannot close the shutter, no external_control configured."
                )
            ret = self._external_ctrl.set("CLOSED")
        elif mode != self.MANUAL:
            raise RuntimeError(
                "Can't close the shutter in %s"
                % self.MODE2STR.get(mode, ("Unknown", "Unknown mode"))[1]
            )
        else:
            ret = self._close()
        self.__state.value = BaseShutterState.CLOSED
        return ret

    def _close(self):
        raise NotImplementedError

    def set_external_control(self, set_open, set_closed, is_open):
        """
        Programmatically set shutter in external control mode,
        and create _external_ctrl switch using callback functions
        """
        if not all(map(callable, (set_open, set_closed, is_open))):
            raise TypeError(
                f"{self.name}.set_external_control: set_open, set_closed, is_open functions must be callable"
            )
        switch = _ShutterSwitch(set_open, set_closed, is_open)
        self._external_ctrl = switch
        self._initialize_with_setting()
