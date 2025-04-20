# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

# PyTango imports
import tango
from tango import Database
from tango.server import run
from tango.server import Device
from tango.server import attribute, command, device_property
from tango import DevState

# Additional import
import os
import pickle
import codecs
from bliss.config.static import get_config

### ====== import bliss to have gevent monkey-patching done ======================
import bliss  # noqa: F401,E402
import gevent.monkey

# revert subprocess monkey-patching


def unpatch_module(module, name):
    """Undo gevent monkey patching of this module

    :param module:
    :param str name: the name given by gevent to this module
    """
    original_module_items = gevent.monkey.saved.pop(name, None)
    if not original_module_items:
        return
    for attr, value in original_module_items.items():
        setattr(module, attr, value)


import subprocess  # noqa: E402


unpatch_module(subprocess, "subprocess")

### =============================================================================

__name__ = os.path.splitext(os.path.split(__file__)[1])[0]
__all__ = ["MotorControllerDevice", "main"]


def find_device(beacon_name, filter="*"):
    db = Database()
    devices = db.get_device_exported(filter).value_string
    for devname in devices:
        bnames = db.get_device_property(devname, "beacon_name")["beacon_name"]
        if bnames:
            if bnames[0] == beacon_name:
                return devname
    return ""


class MotorControllerDevice(Device):
    green_mode = tango.GreenMode.Gevent
    beacon_name = device_property(dtype=str, doc="Object name inside Beacon")

    def init_device(self):
        """Initialises the attributes and properties of the motor controller server."""
        Device.init_device(self)

        config_node = get_config().get_config(self.beacon_name)
        if config_node is None:
            raise RuntimeError(
                f'Unable to serve {self.get_name()}, "{self.beacon_name}" device not found'
            )

        try:
            # "tango-server" is ignored as we want the server to instantiate the real controller here
            config_node.pop("tango-server")
        except KeyError:
            pass

        self._target = get_config().get(self.beacon_name)

        self.set_state(DevState.ON)

    def always_executed_hook(self):
        """Method always executed before any TANGO command is executed."""

    def delete_device(self):
        """Hook to delete resources allocated in init_device.

        This method allows for any memory or other resources allocated in the
        init_device method to be released.  This method is called by the device
        destructor and by the device Init command.
        """

    # === Attributes methods ========================

    @command(dtype_in=str)
    def initialize_axis(self, axis_name):
        axis = self._target.get_axis(axis_name)
        self._target.initialize_axis(axis)

    @command(dtype_in=str, dtype_out=float)
    def read_position(self, axis_name):
        axis = self._target.get_axis(axis_name)
        return self._target.read_position(axis)

    @command(dtype_in=str, dtype_out=float)
    def set_position(self, args_str):
        axis_name, new_position = args_str.split()
        axis = self._target.get_axis(axis_name)
        return self._target.set_position(axis, float(new_position))

    @command(dtype_in=str, dtype_out=float)
    def read_acceleration(self, axis_name):
        axis = self._target.get_axis(axis_name)
        return self._target.read_acceleration(axis)

    @command(dtype_in=str, dtype_out=float)
    def set_acceleration(self, args_str):
        axis_name, new_acceleration = args_str.split()
        axis = self._target.get_axis(axis_name)
        return self._target.set_acceleration(axis, float(new_acceleration))

    @command(dtype_in=str, dtype_out=float)
    def read_velocity(self, axis_name):
        axis = self._target.get_axis(axis_name)
        return self._target.read_velocity(axis)

    @command(dtype_in=str, dtype_out=float)
    def set_velocity(self, args_str):
        axis_name, new_velocity = args_str.split()
        axis = self._target.get_axis(axis_name)
        return self._target.set_velocity(axis, float(new_velocity))

    # use "axis_state" instead of "state" to not conflict with Tango
    @command(dtype_in=str, dtype_out=str)
    def axis_state(self, axis_name):
        axis = self._target.get_axis(axis_name)
        return str(self._target.state(axis))

    @command(dtype_in=str)
    def stop(self, axis_name):
        axis = self._target.get_axis(axis_name)
        self._target.stop(axis)

    @command(dtype_in=str)
    def start_one(self, pickled_motion):
        motion = pickle.loads(codecs.decode(pickled_motion.encode(), "base64"))
        motion._Motion__axis = self._target.get_axis(motion.axis)
        self._target.start_one(motion)

    @command(dtype_in=str, dtype_out=str)
    def get_axis_info(self, axis_name):
        axis = self._target.get_axis(axis_name)
        return self._target.get_axis_info(axis)

    @attribute(dtype=str)
    def controller_axis_settings(self):
        return codecs.encode(
            pickle.dumps(self._target.axis_settings), "base64"
        ).decode()

    @command(dtype_in=str)
    def raw_write(self, com):
        self._target.raw_write(com)

    @command(dtype_in=str, dtype_out=str)
    def raw_write_read(self, com):
        return self._target.raw_write_read(com)


# === Run server ===============================================================


def main(args=None, **kwargs):
    """Main function of the RegulationServer module."""

    # Enable gevents for the server
    kwargs.setdefault("green_mode", tango.GreenMode.Gevent)
    return run((MotorControllerDevice,), args=args, **kwargs)
