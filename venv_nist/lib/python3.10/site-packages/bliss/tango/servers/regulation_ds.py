# -*- coding: utf-8 -*-
#
# This file is part of the RegulationServer project
#
# Copyright (C): 2022
#                European Synchrotron Radiation Facility
#                BP 220, Grenoble 38043
#                France
#
# Distributed under the terms of the GPL license.
# See LICENSE.txt for more info.

""" 

"""
## Version number for checking  compatibilities with SPEC macros.
## Last digit is for bug fixes.
## Any changes on the first two digits breaks the API compatibility.
__versioninfo__ = [1, 0, 0]

import os

__name__ = os.path.splitext(os.path.split(__file__)[1])[0]
__version__ = ".".join(map(str, __versioninfo__))


def get_version():
    return "-".join([__name__, __version__])


# PyTango imports
import tango  # noqa: E402
from tango import DebugIt, Database  # noqa: E402
from tango.server import run  # noqa: E402
from tango.server import Device  # noqa: E402
from tango.server import attribute, command, device_property  # noqa: E402
from tango import DevState, SerialModel  # noqa: E402

# Additional import
# PROTECTED REGION ID(RegulationServer.additionnal_import) ENABLED START #

### ====== import bliss to have gevent monkey-patching done ======================
import bliss  # noqa: F401,E402
import gevent.monkey  # noqa: E402

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

# for _name, _subprocess_item in gevent.monkey.saved["subprocess"].items():
#   setattr(subprocess, _name, _subprocess_item)
### =============================================================================


# PROTECTED REGION END #    //  RegulationServer.additionnal_import

import sys  # noqa: E402
import argparse  # noqa: E402
from numpy import nan as npnan  # noqa: E402
from bliss.config.static import get_config  # noqa: E402
from bliss import global_log  # noqa: E402

SERIAL_MODEL = (
    SerialModel.NO_SYNC
)  # by default tango.GreenMode forces serialization to NO_SYNC

__all__ = ["Loop", "Input", "Output", "main"]


def find_device(beacon_name, filter="*"):
    db = Database()
    devices = db.get_device_exported(filter).value_string
    for devname in devices:
        bnames = db.get_device_property(devname, "beacon_name")["beacon_name"]
        if bnames:
            if bnames[0] == beacon_name:
                return devname
    return ""


class RegulationObject(Device):

    green_mode = tango.GreenMode.Gevent
    beacon_name = device_property(dtype=str, doc="Object name inside Beacon")

    def init_device(self):
        """Initialises the attributes and properties of the RegulationServer."""
        Device.init_device(self)

        self._target = get_config().get(self.beacon_name)

        # === customize server serialisation mode
        util = tango.Util.instance()
        smode = util.get_serial_model()
        if smode != SERIAL_MODEL:
            util.set_serial_model(SERIAL_MODEL)
            print(f"Serialization mode set to {SERIAL_MODEL}")

        # Get log level requiered at DS launch.
        # default=100 -v/v1/v2=500 -v3/v4/v5=600
        logger = self.get_logger()
        tango_log_level = logger.get_level()
        # print(f"TANGO LOG level {self.beacon_name}: {tango_log_level}")

        if tango_log_level > 100:
            try:
                global_log.debugon(self._target.controller)
                if tango_log_level > 500:
                    global_log.debugon(self._target)
            except AttributeError:
                global_log.debugon(self._target)

        self.set_state(DevState.ON)

    def always_executed_hook(self):
        """Method always executed before any TANGO command is executed."""
        # PROTECTED REGION ID(RegulationServer.always_executed_hook) ENABLED START #
        # PROTECTED REGION END #    //  RegulationServer.always_executed_hook

    def delete_device(self):
        """Hook to delete resources allocated in init_device.

        This method allows for any memory or other resources allocated in the
        init_device method to be released.  This method is called by the device
        destructor and by the device Init command.
        """
        # PROTECTED REGION ID(RegulationServer.delete_device) ENABLED START #
        # PROTECTED REGION END #    //  RegulationServer.delete_device

    # === Attributes methods ========================

    @attribute(dtype=str)
    def server_version(self):
        return get_version()

    @attribute(dtype=str)
    def target_name(self):
        return str(self._target.name)

    @attribute(dtype=str)
    def target_info(self):
        return str(self._target.__info__())

    @attribute(dtype=str)
    def target_config(self):
        return str(self._target.config.to_dict())

    @attribute(dtype=str)
    def target_channel(self):
        return str(self._target.channel)

    @attribute(dtype=str)
    def target_unit(self):
        return str(self._target.config.get("unit", ""))

    @attribute(dtype=str)
    def target_mode(self):
        return str(self._target.config.get("mode", "SINGLE"))

    @attribute(dtype=str)
    def target_state(self):
        return str(self._target.state())

    @attribute(dtype=float)
    def target_read(self):
        return float(self._target.read())

    @command(dtype_in=str, dtype_out=str)
    def controller_cmd(self, cmd):
        args = cmd.strip().split()
        if len(args) == 0:
            return "no command provided"

        if hasattr(self._target.controller, args[0]):

            if len(args) > 1:
                try:
                    value = float(args[1])
                except ValueError:
                    value = " ".join(args[1:])

                ans = getattr(self._target.controller, args[0])(value)

            else:
                ans = getattr(self._target.controller, args[0])()

            return str(ans)

        else:
            return f"cannot find controller's attribute: {args[0]}"

    @command(dtype_in=str, dtype_out=str)
    def target_getattr(self, attr):
        if hasattr(self._target, attr):
            return str(getattr(self._target, attr))
        else:
            return f"cannot find attribute: {attr}"

    @command(dtype_in=str, dtype_out=str)
    def target_setattr(self, attr_and_value):
        args = attr_and_value.strip().split()
        if len(args) != 2:
            return "expect argin as a string with format: '{attribute} {value}' "

        attr = args[0]
        try:
            value = float(args[1])
        except ValueError:
            value = str(args[1])

        if hasattr(self._target, attr):
            return str(setattr(self._target, attr, value))
        else:
            return f"cannot find attribute: {attr}"

    @command(dtype_in=str, dtype_out=str)
    def target_call(self, cmd):
        args = cmd.strip().split()
        if len(args) == 0:
            return "no method provided"

        if hasattr(self._target, args[0]):

            if len(args) > 1:
                try:
                    value = float(args[1])
                except ValueError:
                    value = " ".join(args[1:])

                ans = getattr(self._target, args[0])(value)

            else:
                ans = getattr(self._target, args[0])()

            return str(ans)

        else:
            return f"cannot find method: {args[0]}"


class Input(RegulationObject):
    @attribute(dtype=bool)
    def allow_regulation(self):
        return bool(self._target.allow_regulation())


class Output(RegulationObject):
    @attribute(dtype=str)
    def target_mode(self):
        return str(self._target.config.get("mode", "relative"))

    @attribute(dtype=float)
    def limit_low(self):
        if self._target.limits[0] is not None:
            return float(self._target.limits[0])
        else:
            return float("nan")

    @limit_low.write
    def limit_low(self, value):
        lv, hv = self._target._limits
        self._target._limits = (value, hv)

    @attribute(dtype=float)
    def limit_high(self):
        if self._target.limits[1] is not None:
            return float(self._target.limits[1])
        else:
            return float("nan")

    @limit_high.write
    def limit_high(self, value):
        lv, hv = self._target._limits
        self._target._limits = (lv, value)

    @attribute(dtype=float)
    def set_value(self):
        return float(self._target.read())

    @set_value.write
    def set_value(self, value):
        self._target.set_value(value)

    @attribute(dtype=float)
    def ramprate(self):
        return float(self._target.ramprate)

    @ramprate.write
    def ramprate(self, value):
        self._target.ramprate = value

    @attribute(dtype=bool)
    def is_ramping(self):
        return bool(self._target.is_ramping())

    @attribute(dtype=str)
    def range(self):
        if hasattr(self._target, "range"):
            return str(self._target.range)
        else:
            return ""

    @range.write
    def range(self, value):
        if hasattr(self._target, "range"):
            self._target.range = int(value)


class Loop(RegulationObject):
    @attribute(dtype=str)
    def target_input_name(self):
        return str(self._target.input.name)

    @attribute(dtype=str)
    def target_output_name(self):
        return str(self._target.output.name)

    @attribute(dtype=str)
    def target_input_device_name(self):
        devname = find_device(self._target.input.name, "*/regulation/*")
        return str(devname)

    @attribute(dtype=str)
    def target_output_device_name(self):
        devname = find_device(self._target.output.name, "*/regulation/*")
        return str(devname)

    @attribute(dtype=bool)
    def ramp_from_pv(self):
        return bool(self._target._force_ramping_from_current_pv)

    @attribute(dtype=float)
    def deadband(self):
        return float(self._target.deadband)

    @deadband.write
    def deadband(self, value):
        self._target.deadband = value

    @attribute(dtype=float)
    def deadband_time(self):
        return float(self._target.deadband_time)

    @deadband_time.write
    def deadband_time(self, value):
        self._target.deadband_time = value

    @attribute(dtype=float)
    def deadband_idle_factor(self):
        return float(self._target.deadband_idle_factor)

    @deadband_idle_factor.write
    def deadband_idle_factor(self, value):
        self._target.deadband_idle_factor = value

    @attribute(dtype=bool)
    def is_in_deadband(self):
        return bool(self._target.is_in_deadband())

    @attribute(dtype=bool)
    def is_in_idleband(self):
        return bool(self._target.is_in_idleband())

    @attribute(dtype=float)
    def setpoint(self):
        return float(self._target.setpoint)

    @setpoint.write
    def setpoint(self, value):
        self._target.setpoint = value

    @attribute(dtype=float)
    def working_setpoint(self):
        return float(self._target._get_working_setpoint())

    @attribute(dtype=float)
    def kp(self):
        try:
            return float(self._target.kp)
        except (ValueError, TypeError):
            return npnan

    @kp.write
    def kp(self, value):
        self._target.kp = value

    @attribute(dtype=float)
    def ki(self):
        try:
            return float(self._target.ki)
        except (ValueError, TypeError):
            return npnan

    @ki.write
    def ki(self, value):
        self._target.ki = value

    @attribute(dtype=float)
    def kd(self):
        try:
            return float(self._target.kd)
        except (ValueError, TypeError):
            return npnan

    @kd.write
    def kd(self, value):
        self._target.kd = value

    @attribute(dtype=float)
    def sampling_frequency(self):
        if self._target.sampling_frequency is not None:
            spf = self._target.sampling_frequency
        else:
            spf = float("nan")
        return float(spf)

    @sampling_frequency.write
    def sampling_frequency(self, value):
        self._target.sampling_frequency = value

    @attribute(dtype=float)
    def ramprate(self):
        return float(self._target.ramprate)

    @ramprate.write
    def ramprate(self, value):
        self._target.ramprate = value

    @attribute(dtype=bool)
    def is_ramping(self):
        return bool(self._target.is_ramping())

    @attribute(dtype=bool)
    def is_regulating(self):
        return bool(self._target.is_regulating())

    @attribute(dtype=float)
    def axis_position(self):
        return float(self._target.axis_position())

    @axis_position.write
    def axis_position(self, pos):
        self._target.axis_move(pos)

    @attribute(dtype=str)
    def axis_state(self):
        return str(self._target.axis_state())

    @attribute(dtype=str)
    def wait_mode(self):
        return str(self._target.wait_mode)

    @wait_mode.write
    def wait_mode(self, value):
        self._target.wait_mode = value

    @attribute(dtype=float)
    def pid_range_low(self):
        if self._target.pid_range[0] is not None:
            return float(self._target.pid_range[0])
        else:
            return float("nan")

    @pid_range_low.write
    def pid_range_low(self, value):
        l, h = self._target.pid_range
        self._target.pid_range = (value, h)

    @attribute(dtype=float)
    def pid_range_high(self):
        if self._target.pid_range[1] is not None:
            return float(self._target.pid_range[1])
        else:
            return float("nan")

    @pid_range_high.write
    def pid_range_high(self, value):
        l, h = self._target.pid_range
        self._target.pid_range = (l, value)

    @attribute(dtype=str)
    def mode(self):
        if hasattr(self._target, "mode"):
            return str(self._target.mode)
        else:
            return ""

    @mode.write
    def mode(self, value):
        if hasattr(self._target, "mode"):
            self._target.mode = int(value)

    # === Commands =========================

    @command()
    @DebugIt()
    def axis_stop(self):
        self._target.axis_stop()

    @command()
    @DebugIt()
    def stop(self):
        self._target.stop()

    @command()
    @DebugIt()
    def abort(self):
        self._target.abort()

    @command()
    @DebugIt()
    def stop_regulation(self):
        self._target._stop_regulation()

    @command()
    @DebugIt()
    def start_regulation(self):
        self._target._start_regulation(self._target.setpoint)

    @command()
    @DebugIt()
    def stop_ramping(self):
        self._target._stop_ramping()

    @command(
        dtype_in=float,
        doc_in="setpoint",
    )
    @DebugIt()
    def start_ramping(self, value):
        self._target._start_ramping(value)


# === Run server ===============================================================


def main(args=None, **kwargs):
    """Main function of the RegulationServer module."""
    # PROTECTED REGION ID(RegulationServer.main) ENABLED START #

    argv = list(sys.argv if args is None else args)
    argv[0] = os.path.basename(argv[0])  # extract server name from binary absolute path
    if len(argv) < 2 or argv[1] == "-?":  # query available server names
        if len(argv) < 2:
            argv.append("-?")  # if no options, make it behaving like '-?'
        tango.Util(argv)
        return

    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="server personal_name")
    parser.add_argument("-?", action="store_true", help="list available server names")

    parser.add_argument(
        "--serial_model",
        dest="serial_model",
        default="BY_PROCESS",
        choices=["BY_PROCESS", "BY_DEVICE", "NO_SYNC"],
        help="serialization mode for the server",
    )

    options, other_args = parser.parse_known_args()

    tango_args = argv[0:2] + other_args

    global SERIAL_MODEL
    if options.serial_model == "BY_PROCESS":
        SERIAL_MODEL = SerialModel.BY_PROCESS
    elif options.serial_model == "BY_DEVICE":
        SERIAL_MODEL = SerialModel.BY_DEVICE
    elif options.serial_model == "NO_SYNC":
        SERIAL_MODEL = SerialModel.NO_SYNC

    global_log.start_stdout_handler()
    global_log._stdout_handler.setLevel(10)  # set DEBUG level

    # Enable gevents for the server
    kwargs.setdefault("green_mode", tango.GreenMode.Gevent)
    # kwargs.setdefault("verbose", True)
    return run((Loop, Input, Output), args=tango_args, **kwargs)
    # PROTECTED REGION END #    //  RegulationServer.main


if __name__ == "__main__":
    main()
