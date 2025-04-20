# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Actuator is a device with two positions (IN and OUT).
"""
import enum
import gevent
from .event import dispatcher


@enum.unique
class ActuatorState(enum.IntEnum):
    """Actuator States Class"""

    UNKNOWN = 0
    IN = 1
    OUT = 2
    MOVING = 3


class AbstractActuator:
    """Abstract Actuator"""

    def __init__(self, set_in=None, set_out=None, is_in=None, is_out=None):
        self.__in = False
        self.__out = False
        if any((set_in, set_out, is_in, is_out)):
            self._set_in = set_in
            self._set_out = set_out
            self._is_in = is_in
            self._is_out = is_out

    def __info__(self):
        return f"State is {self.state}"

    def wait_ready(self, if_in, timeout=None):
        """Wait until the action has finished.

        Args:
            if_in (bool): True if checking is_in, False if is_out.
            timeout (float): Timeout [s]. None - wait forever, 0 - do nothing.
        Raises:
            Timeout, if timeout > 0
        """
        if timeout == 0:
            return
        action = "IN" if if_in else "OUT"
        with gevent.Timeout(timeout, f"Timeout while setting {action}"):
            if if_in:
                while not self.is_in:
                    gevent.sleep(0.5)
            else:
                while not self.is_out:
                    gevent.sleep(0.5)

    def set_in(self, timeout=None):
        """Set the actuator in position IN

        Args:
           timeout (float): Timeout [s] after which the action is not
                            completed. None - wait forever, 0 - do not wait.
        Raises:
           Timeout: if timeout > 0
        """
        # this is to know which command was asked for,
        # in case we don't have a return (no 'self._is_in' or out)
        self.__in = True
        self.__out = False

        try:
            self._set_in()
            self.wait_ready(if_in=True, timeout=timeout)
        finally:
            dispatcher.send("state", self, self.state)

    def set_out(self, timeout=None):
        """Set the actuator in position OUT

        Args:
           timeout (float): Timeout [s] after which the action is not
                            completed. None - wait forever, 0 - do not wait.
        Raises:
           Timeout: if timeout > 0
        """
        self.__out = True
        self.__in = False

        try:
            self._set_out()
            self.wait_ready(if_in=False, timeout=timeout)
        finally:
            dispatcher.send("state", self, self.state)

    @property
    def is_in(self):
        """Check if the actuator is in position IN.

        Returns:
            (bool): True if IN, False otherwise
        """
        if self._is_in is not None:
            ret = self._is_in()
            if ret is not None:
                return self._is_in()
        else:
            if self._is_out is not None:
                ret = self._is_out()
                if ret is not None:
                    return not self._is_out()
        return self.__in

    @property
    def is_out(self):
        """Check if the actuator is in position OUT.

        Returns:
            (bool): True if OUT, False otherwise
        """
        if self._is_out is not None:
            ret = self._is_out()
            if ret is not None:
                return self._is_out()
        else:
            if self._is_in is not None:
                ret = self._is_in()
                if ret is not None:
                    return not self._is_in()
        return self.__out

    def toggle(self, timeout=None):
        """Toggle between IN/OUT.

        Args:
           timeout (float): Timeout [s] after which the action is not
                            completed. None - wait forever, 0 - do not wait.
        Raises:
           Timeout: if timeout > 0
        """
        if self.is_in:
            self.set_out(timeout)
        elif self.is_out:
            self.set_in(timeout)

    @property
    def state(self):
        """Get the state of the actuator.

        Returns:
            (str): The state of the actuator
        """
        state = ActuatorState.UNKNOWN
        if self.is_in:
            state += ActuatorState.IN
        if self.is_out:
            state += ActuatorState.OUT
        for _st in ActuatorState:
            if state == _st:
                return _st.name
        return ActuatorState.UNKNOWN.name

    # Sometimes it is more natural to use open/close than set_in/set_out

    def close(self, timeout=None):
        """Open means beam after the actuator"""
        self.set_out(timeout)

    def open(self, timeout=None):
        """Close means no beam after the actuator"""
        self.set_in(timeout)

    # Context manager methods

    def __enter__(self):
        self.set_in()

    def __exit__(self, *args):
        self.set_out()
