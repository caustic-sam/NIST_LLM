#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Device server to be used with the Keithley 6485 bliss controller.
The only property is 'beacon_name', which is the name, given to the bliss
object.
"""

from tango import DevState, GreenMode
from tango.server import Device, device_property, attribute, command, run

from bliss.config.static import get_config


def switch_state(tg_dev, state=None, status=None):
    """Helper to switch state and/or status and send event"""
    if state is not None:
        tg_dev.set_state(state)
        #        tg_dev.push_change_event("state")
        if state in (DevState.ALARM, DevState.UNKNOWN, DevState.FAULT):
            msg = "State changed to " + str(state)
            if status is not None:
                msg += ": " + status
    if status is not None:
        tg_dev.set_status(status)


class Multimeter(Device):
    """Device server implementation."""

    beacon_name = device_property(dtype=str, doc="keithley bliss object name")

    def __init__(self, *args):
        self.device = None
        super().__init__(*args)
        self.init_device()

    def init_device(self):
        """Initialise the tango device"""
        super().init_device()

        try:
            self.device = get_config().get(self.beacon_name)
            # force the configuration of the keithley
            self.reset()
            switch_state(self, DevState.ON, "Ready!")
        except AttributeError as err:
            msg = f"Exception initializing device: {err}"
            self.error_stream(msg)
            switch_state(self, DevState.FAULT, msg)

    def delete_device(self):
        """Delete the device"""
        if self.device:
            self.device.abort()

    @command
    def abort(self):
        """Abort the acquisition"""
        self.device.abort()

    @command
    def acquire_zero_correct(self):
        """Procedure to acquire the zero correct value."""
        self.device.acquire_zero_correct()

    @command
    def reset(self):
        """Force configuration after keithle being switched off and on"""
        self.device.controller.apply_config()

    @attribute(dtype=str)
    def info(self):
        """Get the device information."""
        model = f"model = {self.device.controller.config['model']}\n"
        return model + self.device.__info__()

    @attribute(dtype=bool)
    def auto_range(self):
        """Get the autorange status.
        Retuns:
            (bool): True if set, False otherwise.
        """
        return self.device.auto_range

    @auto_range.setter
    def auto_range(self, value):
        """Set the autorange.
        Args:
            value(bool): True to set, False otherwise.
        """
        self.device.auto_range = value

    @attribute(dtype=bool)
    def auto_zero(self):
        """Get the auto zeroing status.
        Retuns:
            (bool): True if set, False otherwise.
        """
        return self.device.auto_zero

    @auto_zero.setter
    def auto_zero(self, value):
        """Set the auto zeroing.
        Args:
            value(bool): True to set, False otherwise.
        """
        self.device.auto_zero = value

    @attribute(dtype=float)
    def range(self):
        """Read the current range.
        Retuns:
            (float): Current range [V].
        """
        return self.device.range

    @range.setter
    def range(self, value):
        """Set the current range. Warning: this cancels the auto range.
        Args:
            value(float): Range [V]
        """
        self.device.range = value

    @attribute(dtype=str)
    def possible_ranges(self):
        """Get the possible range values.
        Returns:
            (list): the available ranges.
        """
        return str(self.device.possible_ranges)

    @attribute(dtype=float)
    def nplc(self):
        """Read the integration rate - number of power line cycles (NPLC).
           E.g. 1 PLC for 50Hz is 20msec (1/50). Global for all range
        Returns:
            (float): The number of power line cycles.
        """
        return self.device.nplc

    @nplc.setter
    def nplc(self, value):
        """Read the integration rate - number of power line cycles (NPLC).
           E.g. 1 PLC for 50Hz is 20msec (1/50). Global for all range.
        Args:
            value(float): The number of power line cycles.
        """
        self.device.nplc = value

    @attribute(dtype=bool)
    def zero_check(self):
        """Get the zero check (shunt the input signal to low) status.
        Retuns:
            (bool): True if set, False otherwise.
        """
        return self.device.zero_check

    @zero_check.setter
    def zero_check(self, value):
        """Set the zero check.
        Args:
            value(bool): True to set, False otherwise.
        """
        self.device.zero_check = value

    @attribute(dtype=bool)
    def zero_correct(self):
        """Get the zero correction (subtract the voltage offset term) status.
        Retuns:
            (bool): True if set, False otherwise.
        """
        return self.device.zero_correct

    @zero_correct.setter
    def zero_correct(self, value):
        """Set the zero correction (subtract the voltage offset term).
        Args:
            value(bool): True to set, False otherwise.
        """
        self.device.zero_correct = value

    @attribute(dtype=float)
    def raw_read(self):
        """Read the acquisition data.
        Retuns:
            (float): The acquisition value [V].
        """
        return self.device.raw_read


def main():
    """
    import logging

    fmt = "%(levelname)s %(asctime)-15s %(name)s: %(message)s"
    logging.basicConfig(format=fmt, level=logging.DEBUG)
    """
    run([Multimeter], green_mode=GreenMode.Gevent)


if __name__ == "__main__":
    main()
