# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Control Tango safety shutter, front end shutter or valve.

Example yml file:

.. code-block:: yaml

    -
      # safety shutter
      class: TangoShutter
      name: safshut
      uri: id42/bsh/1


Overloading `is_open` and `is_closed` properties to deal with the Tango states
as follows:

.. code-block::

    FrontEnd (accelerator FrontEnd device server)
        The state of a Tango FrontEnd server can be (according to JLP):

        * Tango::OPEN:    FrontEnd is open with the automatic mode disabled
        * Tango::RUNNING: FrontEnd is open with the automatic mode enabled
        => OPEN

        * Tango::CLOSE:   FrontEnd is close with the injection mode disabled
        * Tango::STANDBY: FrontEnd is close with the injection mode enabled
        => CLOSED

        * Tango::FAULT:   FrontEnd in fault
        * Tango::DISABLE: No operation permission
        => ???

    SafetyShutter (PLCvacuumValve device server)
        The state of a Safety Shutter Tango server can be:

        * Tango::OPEN
        => OPEN

        * Tango::CLOSE (not CLOSED)
        * Tango::DISABLE
        => CLOSED

        * ON OFF INSERT EXTRACT MOVING STANDBY FAULT INIT RUNNING ALARM UNKNOWN
        => ???

    Valve (PLCvacuumValve device server)
        The state of a Valve Tango server can be:

        * Tango::OPEN
        => OPEN

        * Tango::CLOSE (not CLOSED)
        * Tango::DISABLE
        => CLOSED

        * ON OFF INSERT EXTRACT MOVING STANDBY FAULT INIT RUNNING ALARM UNKNOWN
        => ???
"""

from __future__ import annotations

from gevent import Timeout, sleep
from bliss import global_map
from bliss.common.shutter import BaseShutter, BaseShutterState
from bliss.common.tango import DeviceProxy, DevFailed
from bliss.common.tango_callbacks import TangoCallbacks
from bliss.common.logtools import log_warning
from bliss.config.channels import Channel
from bliss.common import event


# Compatibility with BLISS <= 1.10
TangoShutterState = BaseShutterState


_tangoStateToBlissState = {
    "OPEN": BaseShutterState.OPEN,
    "CLOSE": BaseShutterState.CLOSED,
    "MOVING": BaseShutterState.MOVING,
    "RUNNING": BaseShutterState.RUNNING,
    "STANDBY": BaseShutterState.STANDBY,
    "FAULT": BaseShutterState.FAULT,
    "DISABLE": BaseShutterState.DISABLE,
}

TANGO_OPEN_STATES = {
    "SafetyShutter": ["OPEN", "RUNNING"],
    "FrontEnd": ["OPEN", "RUNNING"],
    "Valve": ["OPEN", "RUNNING"],
    "Generic": ["OPEN", "RUNNING"],
}

TANGO_CLOSED_STATES = {
    "SafetyShutter": ["CLOSE", "STANDBY", "FAULT"],
    "FrontEnd": ["CLOSE", "STANDBY"],
    "Valve": ["CLOSE", "STANDBY", "FAULT", "DISABLE"],
    "Generic": ["CLOSE", "STANDBY"],
}


class TangoShutter(BaseShutter):
    """Handle Tango frontend or safety shutter or a valve"""

    def __init__(self, name, config, shutter_type=None):
        self._tango_uri = config.get("uri")

        self.__name = name
        self.__config = config
        self.__control = DeviceProxy(self._tango_uri)
        self.__callbacks = TangoCallbacks(self.__control)
        self.__callbacks.add_callback("state", self._tango_state_changed)
        self.__shutter_type = shutter_type or config.get("shutter_type")
        if not self.__shutter_type:
            self.__shutter_type = self._guess_type()

        global_map.register(self, children_list=[self.__control], tag=f"Shutter:{name}")
        self._mode = None

        self._state_channel = Channel(
            f"{name}:state", default_value="UNKNOWN", callback=self._state_changed
        )

    def _guess_type(self):
        """
        Try to guess what is the shutter type considering the tango class of the
        device (if available) or the device name.

        Returns:
            (str): "FrontEnd", "SafetyShutter", "Valve" or "Generic".
        """
        if "FrontEnd" in self.__control.info().dev_class:
            return "FrontEnd"
        if "rv" in self.__control.dev_name().lower():
            return "Valve"
        if "bsh" in self.__control.dev_name().lower():
            return "SafetyShutter"
        return "Generic"

    @property
    def shutter_type(self):
        """Get shutter type.

        Returns:
            (str): "FrontEnd", "SafetyShutter", "Valve" or "Generic".
        """
        return self.__shutter_type

    @property
    def is_open(self) -> bool:
        """Check if the Tango Shutter is open

        Returns: True if open, False otherwise.
        """
        state = self._tango_state
        is_open = state in TANGO_OPEN_STATES[self.shutter_type]
        is_closed = state in TANGO_CLOSED_STATES[self.shutter_type]
        if is_open and is_open == is_closed:
            print(
                f"WARNING: {self.shutter_type} state coherency problem: state is : {state}"
            )
        return is_open

    @property
    def is_closed(self) -> bool:
        """Check if the Tango Shutter is closed.

        Returns: True if closed, False otherwise.
        """
        state = self._tango_state
        is_open = state in TANGO_OPEN_STATES[self.shutter_type]
        is_closed = state in TANGO_CLOSED_STATES[self.shutter_type]
        if is_open and is_open == is_closed:
            print(
                f"WARNING: {self.shutter_type} state coherency problem: state is : {state}"
            )
        return is_closed

    @property
    def proxy(self):
        """Return the proxy to the device server"""
        return self.__control

    @property
    def name(self) -> str:
        """A unique name"""
        return self.__name

    @property
    def config(self):
        """Config of shutter"""
        return self.__config

    @property
    def _tango_state(self) -> str:
        """Read the tango state.

        PyTango states: 'ALARM', 'CLOSE',
        'DISABLE', 'EXTRACT', 'FAULT', 'INIT', 'INSERT', 'MOVING', 'OFF',
        'ON', 'OPEN', 'RUNNING', 'STANDBY', 'UNKNOWN'.

        Returns:
            The state read from the device server.
        """
        return self.__control.state().name

    @property
    def _tango_status(self):
        """Read the status.

        Returns:
            (str): Complete status from the device server.
        """
        return self.__control.status()

    @property
    def state(self) -> BaseShutterState:
        """Read the state. Attention, values differ from Tango States.

        Returns:
            (enum): state as enum
        Raises:
            RuntimeError: If DevFailed from the device server
        """
        try:
            state = self._tango_state
        except DevFailed as tango_exc:
            _msg = f"Communication error with {self.__control.dev_name()}"
            raise RuntimeError(_msg) from tango_exc
        return _tangoStateToBlissState.get(state, BaseShutterState.UNKNOWN)

    def _state_changed(self, stat):
        """Send a signal when state changes"""
        event.send(self, "state", stat)

    def _tango_state_changed(self, attr_name, value):
        state = _tangoStateToBlissState.get(value.name, BaseShutterState.UNKNOWN)
        event.send(self, "state", state)

    @property
    def state_string(self) -> tuple[str, str]:
        """Return state as combined string.

        Returns
            (tuple): state as string, tango status
        """
        return self.state.value, self._tango_status

    def __info__(self):
        info_str = f"{self.shutter_type} `{self.name}` ({self._tango_uri}): {self.state.value}\n"
        info_str += self._tango_status
        return info_str

    def open(self, timeout: float | None = 60):
        """Open this shutter

        Args:
            timeout: Timeout [s] to wait until execution finished.
                     If timeout == 0: return at once and do not wait.
                     if timeout is None: wait forever.
        Raises:
            RuntimeError: Cannot execute if device in wrong state
        """
        state = self.state
        if state.name in TANGO_OPEN_STATES[self.shutter_type]:
            log_warning(self, f"{self.name} already open, command ignored")
        elif state == BaseShutterState.DISABLE:
            log_warning(self, f"{self.name} disabled, command ignored")
        elif state == BaseShutterState.CLOSED:
            self.__control.open()
            self._wait(BaseShutterState.OPEN, timeout)
            print(f"{self.name} was {state.name} and is now {self.state.name}")
        else:
            raise RuntimeError(
                f"Cannot open {self.name}, current state is: {state.value}"
            )

    def close(self, timeout: float | None = 60):
        """Close this shutter.

        Args:
            timeout: Timeout [s] to wait until execution finished.
                     If timeout == 0: return at once and do not wait.
                     if timeout is None: wait forever.
        Raises:
            RuntimeError: Cannot execute if device in wrong state
        """
        state = self.state
        if state == BaseShutterState.CLOSED:
            log_warning(self, f"{self.name} already closed, command ignored")
        elif state == BaseShutterState.DISABLE:
            log_warning(self, f"{self.name} disabled, command ignored")
        elif state.name in TANGO_OPEN_STATES[self.shutter_type]:
            self.__control.close()
            self._wait(BaseShutterState.CLOSED, timeout)
            print(f"{self.name} was {state.name} and is now {self.state.name}")
        else:
            raise RuntimeError(
                f"Cannot close {self.name}, current state is: {state.value}"
            )

    @property
    def mode(self):
        """
        Get or set the opening mode of the FrontEnd.

        state is read from tango attribute: `automatic_mode`.
        Only available for FrontEnd shutters.

        Parameters:
            mode: (str): 'MANUAL' or 'AUTOMATIC'

        Raises:
            NotImplementedError: Not a Frontend shutter
        """
        if self.shutter_type != "FrontEnd":
            raise NotImplementedError("Not a Frontend shutter")

        try:
            _mode = self.__control.automatic_mode
        except AttributeError:
            _mode = None
        self._mode = "AUTOMATIC" if _mode else "MANUAL" if _mode is False else "UNKNOWN"
        return self._mode

    @mode.setter
    def mode(self, mode):
        if self.shutter_type != "FrontEnd":
            raise NotImplementedError("Not a Frontend shutter")

        try:
            if mode == "MANUAL":
                self.__control.manual()
            elif mode == "AUTOMATIC":
                self.__control.automatic()
            else:
                raise RuntimeError(f"Unknown mode: {mode}")

            self._wait_mode(mode=mode)
        except DevFailed as df_err:
            raise RuntimeError(f"Cannot set {mode} opening") from df_err

    def reset(self, timeout: float | None = 3):
        """Reset the shutter.

        Args:
           (float): Timeout [s] to wait until execution finished.
                     If timeout == 0: return at once and do not wait
                     if timeout is None: wait forever.
        Raises:
            RuntimeError: Cannot execute the command.
        """
        self.__control.Reset()
        if self.shutter_type != "FrontEnd":
            # after reset on safety shutter the state is closed
            self._wait(BaseShutterState.CLOSED, timeout)

    def _wait(self, state: BaseShutterState, timeout: float | None = 0):
        """Wait execution to finish.

        Args:
            (enum): state
            (float): timeout [s]. If timeout is None - wait forever
                                  If timeout == 0 - return immediately
        Raises:
            RuntimeError: Execution timeout.
        """
        if timeout == 0:
            return

        # sleep(3) to get rid of strange intermediate states
        sleep(3)

        with Timeout(timeout, RuntimeError("Execution timeout")):
            while self.state != state:
                sleep(1)
            self._state_channel.value = state

    def _wait_mode(self, mode, timeout: float | None = 3):
        """
        Wait until set mode is equal to read mode.

        Args:
            mode(str): "AUTOMATIC" or "MANUAL".
            timeout(float): Timeout [s].
        Raises:
            RuntimeError: Timeout while setting the mode.
        """
        # this method to be removed when FE tango server fixed.
        # FE tango server feature: 'automatic_mode' goes True even
        # if it's not allowed (ex: MDT)
        # It switches back to False after ~1 second.
        # So this method can return without error even if AUTOMATIC
        # mode is not set properly.
        sleep(3)
        with Timeout(timeout, RuntimeError(f"Cannot set {mode} opening mode")):

            while self.mode != mode:
                sleep(0.5)

    def __del__(self):
        """Called by Python at the release of the object"""
        self.__callbacks.stop()

    def __close__(self):
        """Called by BLISS at the release of the session"""
        self.__callbacks.stop()
