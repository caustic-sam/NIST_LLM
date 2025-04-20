# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Mockup controller shutter.

Example yml file:

.. code-block:: yaml

    - name:  myshutter1
      plugin: bliss
      class: MockupShutter
      package: bliss.controllers.shutters.mockup

      # Initial state of the shutter
      state: CLOSED  # Default is UNKNOWN

      # Behaviour of the shutter
      opening_time: 1    # In second, default is 0
      closing_time: 0.5  # In second, default is 0
"""

import gevent
from bliss.common.shutter import BaseShutter, BaseShutterState
from bliss.config.beacon_object import BeaconObject, EnumProperty


class MockupShutter(BeaconObject, BaseShutter):
    def __init__(self, name=None, config={}):
        BeaconObject.__init__(self, config)
        config = dict(config)

        # Drop default keys
        config.pop("name", None)
        config.pop("plugin", None)
        config.pop("package", None)
        config.pop("class", None)

        self._closing_time = config.pop("closing_time", 0)
        self._opening_time = config.pop("opening_time", 0)
        state = config.pop("state", BaseShutterState.UNKNOWN.name)

        # Initialization
        self._name = name
        if self.state is None:
            self.state = state

        if len(config) > 0:
            keys = "', '".join(config.keys())
            raise RuntimeError(
                f"Keys '{keys}' are not supported by {type(self).__qualname__}"
            )

        self._greenlet = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        self._name = name

    state = EnumProperty(
        "state", enum_type=BaseShutterState, default=None, doc="State of this device"
    )

    @property
    def closing_time(self):
        """
        Returns the time taken to close this shutter.

        From `Shutter` API.
        """
        return self._closing_time

    @property
    def opening_time(self):
        """
        Returns the time taken to open this shutter.

        From `Shutter` API.
        """
        return self._opening_time

    def measure_open_close_time(self):
        """
        Procedure to mesure the time to open and close this shutter.

        From `Shutter` API.
        """
        pass

    def _set_state_later(self, new_state, delay=None):
        """Sleep then set this new state"""
        if delay is not None and delay > 0:
            gevent.sleep(delay)
        self.state = new_state

    def open(self, timeout=None):
        """Opens this shutter

        Argument:
            timeout: If None wait until the shutter is open
        """
        if self._greenlet is not None:
            self._greenlet.kill()
        self.state = BaseShutterState.MOVING
        if timeout:
            self._greenlet = gevent.spawn(
                self._set_state_later, BaseShutterState.OPEN, self._opening_time
            )
            gevent.sleep(timeout)
        else:
            gevent.sleep(self._opening_time)
            self.state = BaseShutterState.OPEN

    def close(self, timeout=None):
        """Closes this shutter

        Argument:
            timeout: If None wait until the shutter is closed
        """
        if self._greenlet is not None:
            self._greenlet.kill()
        self.state = BaseShutterState.MOVING
        if timeout:
            self._greenlet = gevent.spawn(
                self._set_state_later, BaseShutterState.CLOSED, self._closing_time
            )
            gevent.sleep(timeout)
        else:
            gevent.sleep(self._closing_time)
            self.state = BaseShutterState.CLOSED

    def __info__(self):
        return BaseShutter.__info__(self)
