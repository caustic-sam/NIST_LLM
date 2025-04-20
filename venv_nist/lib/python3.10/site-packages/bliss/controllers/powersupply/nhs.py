# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
ISEG NHS power supply. It uses only an USB communication.

yml configuration example:

- class: NhsController
  module: powersupply.nhs
  plugin: generic
  name: nhs60n
  tcp:
    url: lid00limax:28400

  axes:
    - name: nhs_out0
      class: NhsAxis
      channel: 0
      low_limit: -1000
      high_limit: 1
      tolerance: 1.1
      velocity: 20

    - name: nhs_out1
      class: NhsAxis
      channel: 1
      low_limit: -1000
      high_limit: 0
      tolerance: 1
      velocity: 20

    - name: nhs_out2
      class: NhsAxis
      channel: 2
      low_limit: -1000
      high_limit: 0
      tolerance: 1
      velocity: 20

    - name: nhs_out3
      class: NhsAxis
      channel: 3
      low_limit: -1100
      high_limit: 1
      tolerance: 1
      velocity: 20

    - name: nhs_out4
      class: NhsAxis
      channel: 4
      low_limit: -1100
      high_limit: 1
      tolerance: 1
      velocity: 20


    - name: nhs_out5
      class: NhsAxis
      channel: 5
      low_limit: -1100
      high_limit: 1
      tolerance: 1
      velocity: 20
"""

import math

import gevent

from bliss import global_map
from bliss.comm.util import get_comm
from bliss.common.utils import autocomplete_property
from bliss.common.counter import SamplingCounter
from bliss.common.logtools import log_info
from bliss.controllers.counter import SamplingCounterController
from bliss.common.axis.state import AxisState
from bliss.common.axis.axis import lazy_init
from bliss.controllers.motors.soft import SoftAxis
from bliss.common.protocols import CounterContainer

from bliss.controllers.motor import Controller


class NhsSCC(SamplingCounterController):
    def __init__(self, name, bctrl):
        super().__init__(name)
        self.bctrl = bctrl

    def read(self, counter):
        if counter.unit == "V":
            return self.bctrl.hardware.get_meas_voltage(counter.channel)

        if counter.unit == "A":
            return self.bctrl.hardware.get_meas_current(counter.channel)

        raise ValueError(f"Wrong unit for counter {counter.name}")


class NhsAxis(SoftAxis):
    @lazy_init
    def __info__(self):
        """Standard method called by BLISS Shell info helper:
        Return common axis information about the axis.
        PLUS controller specific information.
        """
        info_string = "AXIS:\n"

        try:
            # Config parameters.
            info_string += f"     name (R): {self.name}\n"
            info_string += f"     unit (R): {self.unit}\n"
            # info_string += f"     offset (R): {self.offset:.5f}\n"
            # info_string += f"     backlash (R): {self.backlash:.5f}\n"
            # info_string += f"     sign (R): {self.sign}\n"
            # info_string += f"     steps_per_unit (R): {self.steps_per_unit:.2f}\n"
            info_string += (
                f"     tolerance (R) (to check pos. before a move): {self.tolerance}\n"
            )

            if self.motion_hooks:
                info_string += "     motion_hooks (R):\n"
                for hook in self.motion_hooks:
                    info_string += f"          {hook}\n"
            else:
                info_string += "     motion_hooks (R): []\n"

            _low_cfg_limit, _high_cfg_limit = self.config_limits
            _lim = f"Low: {self.low_limit:.5f} High: {self.high_limit:.5f}"
            _cfg_lim = f"(config Low: {_low_cfg_limit:.5f} High: {_high_cfg_limit:.5f})"
            info_string += f"     limits (RW):    {_lim}    {_cfg_lim}\n"
            info_string += f"     dial (RW): {self.dial:.5f}\n"
            info_string += f"     position (RW): {self.position:.5f}\n"
        except Exception:
            info_string += "ERROR: unable to get info\n"

        try:
            info_string += f"     state (R): {self.state}\n"
        except Exception:
            info_string += "     ERROR: unable to get state\n"

        # VELOCITY
        try:
            _vel = self.velocity

            if self.controller.axis_settings.config_setting["velocity"]:
                _vel_config = f"{self.config_velocity:10.5f}"
            else:
                _vel_config = "none"

            info_string += (
                f"     velocity (RW):     {_vel:10.5f}  (config: {_vel_config})\n"
            )
            # velocity limits
            vel_low, vel_high = self.velocity_limits
            vel_config_low, vel_config_high = self.config_velocity_limits
            if vel_low is not None and vel_low != math.inf:
                info_string += f"     velocity_low_limit (RW):     {vel_low:10.5f}  (config: {vel_config_low})\n"
            if vel_high is not None and vel_low != math.inf:
                info_string += f"     velocity_high_limit (RW):     {vel_high:10.5f}  (config: {vel_config_high})\n"
        except Exception:
            info_string += "     velocity: None\n"

        status_dict = self.controller.hardware.status_channel(self.channel)
        if status_dict["is current limit"]:
            curr_state = "is"
        else:
            curr_state = "is not"

        info_string += " " * 5 + f"{curr_state} in current limit\n"

        return info_string

    @property
    def channel(self):
        return self.config.config_dict["channel"]

    @property
    def on(self):
        """Return True if the channel is on"""
        return self.controller.hardware.get_is_on(self.channel)

    @on.setter
    def on(self, value):
        if value:
            self.controller.hardware.set_is_on(self.channel, "ON")
        else:
            self.controller.hardware.set_is_on(self.channel, "OFF")

    @property
    def ramprate_voltage(self):
        return self.controller.hardware.get_ramprate_voltage(self.channel)

    @ramprate_voltage.setter
    def ramprate_voltage(self, value):
        self.controller.hardware.set_ramprate_voltage(self.channel, value)

    @property
    def current_limit(self):
        return self.controller.hardware.get_set_current_limit(self.channel)

    @current_limit.setter
    def current_limit(self, value):
        self.controller.hardware.set_set_current_limit(self.channel, value)

    @property
    def voltage_limit(self):
        return self.controller.hardware.get_set_voltage_limit(self.channel)

    @voltage_limit.setter
    def voltage_limit(self, value):
        self.controller.hardware.set_set_voltage_limit(self.channel, value)

    @property
    def status(self):
        # Get full channel status
        status = self.controller.hardware.status_channel(self.channel)
        for key, value in status.items():
            print(f"\t{key} = {value}")

    @property
    def events(self):
        """A "False" answer means OK"""
        status = {}
        # Get full events status
        status = self.controller.hardware.events_channel(self.channel)
        for key, value in status.items():
            print(f"\t{key} = {value}")

    @property
    def events_clear(self):
        self.controller.hardware.events_clear_channel(self.channel)


class NhsController(Controller, CounterContainer):
    _VALID_SWITCH = ["on", "ON", "off", "OFF", 1, 0]

    def __init__(self, config):
        super().__init__(config)
        self.__config = config
        self._hw_controller = None
        self._myscc = NhsSCC(self.name, self)
        global_map.register(self, parent_list=["controllers", "counters"])

    def __info__(self):

        from bliss.shell.formatters.table import IncrementalTable

        info_list = []
        comm_config = self.__config.get("serial", {})
        comm_config = self.__config.get("tcp", comm_config)

        print(f"\nGathering information from {comm_config}:\n")

        # Get full identification string
        identifier_dict = self.hardware.module_info
        for key, value in identifier_dict.items():
            info_list.append(f"{key} = {value}")

        txt = "\n".join(info_list)

        if self.axes:
            labels = [
                "Channel",
                "on",
                "Voltage value",
                "State",
                " Current limit ",
                " Voltage limit ",
                "velocity (ramp rate)",
            ]
            tab = IncrementalTable([labels], col_sep="|", flag="")

            for axis in self.axes.values():
                status_dict = self.hardware.status_channel(axis.channel)
                velocity = self.hardware.get_ramprate_voltage(axis.channel)
                voltage_value = self.hardware.get_meas_voltage(axis.channel)

                state = str(axis.state)
                statel = state.split()
                state = statel[0].upper()

                if status_dict["is current limit"]:
                    curr_state = "YES"
                else:
                    curr_state = "NO"

                if status_dict["is voltage limit"]:
                    volt_state = "YES"
                else:
                    volt_state = "NO"

                tab.add_line(
                    [
                        axis.channel,
                        axis.on,
                        voltage_value,
                        state,
                        curr_state,
                        volt_state,
                        velocity,
                    ]
                )

            tab.resize(9, 22)
            tab.add_separator("-", line_index=1)
            txt += f"\n\n{str(tab)}"

        return txt

    @autocomplete_property
    def hardware(self):
        if self._hw_controller is None:
            self._hw_controller = NhsDevice(self._config)
        return self._hw_controller

    @autocomplete_property
    def counters(self):
        return self._myscc.counters

    def _create_axis_subitem(self, name, cfg, parent_key, item_class, item_obj=None):
        cfg.setdefault("steps_per_unit", 1)
        axis = super()._create_axis_subitem(name, cfg, parent_key, item_class, item_obj)

        # create associated counter
        channel = cfg["channel"]
        mode = cfg.get("mode", "MEAN")
        cntv = SamplingCounter(f"{name}_V", self._myscc, mode=mode, unit="V")
        cntv.unit = "V"
        cntv.channel = channel
        cntc = SamplingCounter(f"{name}_C", self._myscc, mode=mode, unit="A")
        cntc.unit = "A"
        cntc.channel = channel

        return axis

    def initialize(self):
        # steps_per_unit and acceleration are not mandatory in config
        # self.axis_settings.config_setting["velocity"] = False
        self.axis_settings.config_setting["acceleration"] = False
        self.axis_settings.config_setting["steps_per_unit"] = False

    def initialize_axis(self, axis):
        self.hardware.comm.flush()

    def steps_position_precision(self, axis):
        """
        Return -inf to bypass _is_already_on_position() check.
        """
        return float("-inf")

    def state(self, axis):
        status_dict = self.hardware.status_channel(axis.channel)
        if axis.on != 1:
            return AxisState("OFF")
        elif status_dict["is voltage ramp"]:
            return AxisState("MOVING")
        else:
            return AxisState("READY")

    def start_one(self, motion):
        channel = motion.axis.channel
        self.hardware.set_set_voltage(channel, motion.target_pos)

    def start_all(self, *motion_list):
        for motion in motion_list:
            self.start_one(motion)

    def read_position(self, axis):
        return self.hardware.get_meas_voltage(axis.channel)

    def stop(self, axis):
        diff = abs(
            self.read_position(axis) - self.hardware.get_set_voltage(axis.channel)
        )
        if diff <= axis.tolerance:
            return

    def read_velocity(self, axis):
        return axis.ramprate_voltage

    def set_velocity(self, axis, new_velocity):
        axis.ramprate_voltage = new_velocity

    def kill_enable(self, value=None):
        """Return 1 if kill is enable"""

        if value is None:
            return self.hardware.get_kill_enable()
        else:
            if value in self._VALID_SWITCH:
                if value == "OFF" or value == "off":
                    value = 0
                elif value == "ON" or value == "on":
                    value = 1

                self.hardware.set_kill_enable(value)
            else:
                raise ValueError(
                    f'Wrong value "{value}". Value should be in {self._VALID_SWITCH}.'
                )

    def reset(self):
        self.hardware.reset()


class NhsDevice:
    """
    iseg modules compatible are :
    EHS, NHR, SHR, NHS and MICC.
    """

    def __init__(
        self,
        config,
    ):
        self._config = config
        self._comm = None
        self._timeout = config.get("timeout", 3.0)
        self._lock = gevent.lock.RLock()
        self.channel = None
        self.tag = None

    @property
    def comm(self):
        if self._comm is None:
            self._comm = get_comm(self._config)
        return self._comm

    def _initialize(self):
        """Initialize/reset communication layer and synchronize with hardware"""
        self.send_cmd("*CLS")
        self.channel = self._config.get("channel")
        self.tag = self._config.get("tag")

    @property
    def module_info(self):
        """
        Get the module identifier.
        Return: {manufacturer, instrument_type, serial_number, firmware_version}
        """
        log_info(self, "identification_getter")
        info = self.send_cmd("*IDN?").split(",")
        manufacturer = f"{info[0]}"
        instrument_type = info[1]
        serial_number = info[2]
        firmware_version = info[3]
        return {
            "Manufacturer": manufacturer,
            "Instrument type": instrument_type,
            "Serial number": serial_number,
            "Firmware version": firmware_version,
        }

    def clear(self):
        """Clear the module event status and all event status registers."""
        log_info(self, "clear")
        self.send_cmd("*CLS")

    def reset(self):
        """
        Reset the device to save values:
        * turn high voltage off with ramp for all channel ;
        * set nominal voltage (V_set) to zero for all channels ;
        * set current (I_set) to the current nominal for all channels (unused).
        """
        log_info(self, "reset")
        self.send_cmd("*RST")

    def get_set_voltage(self, channel):
        return self.send_cmd("READ:VOLT?", channel=channel)

    def set_set_voltage(self, channel, value):
        self.send_cmd("VOLT", channel=channel, arg=value)

    def get_set_current(self, channel):
        return self.send_cmd("READ:CURR?", channel=channel)

    def set_set_current(self, channel, value):
        self.send_cmd("CURR", channel=channel, arg=value)

    def get_set_current_limit(self, channel):
        return self.send_cmd("READ:CURR:LIM?", channel=channel)

    def set_set_current_limit(self, channel, value):
        self.send_cmd("CURR:LIM", channel=channel, arg=value)

    def get_set_voltage_limit(self, channel):
        return self.send_cmd("READ:VOLT:LIM?", channel=channel)

    def set_set_voltage_limit(self, channel, value):
        self.send_cmd("VOLT:LIM", channel=channel, arg=value)

    def get_meas_voltage(self, channel):
        return self.send_cmd("MEAS:VOLT?", channel=channel)

    def get_meas_current(self, channel):
        return self.send_cmd("MEAS:CURR?", channel=channel)

    def get_ramprate_voltage(self, channel):
        """Read the voltage set ramprate (V/s) of the given channel"""
        log_info(self, "get_ramprate_voltage")
        return self.send_cmd("READ:RAMP:VOLT?", channel=channel)

    def set_ramprate_voltage(self, channel, value):
        """Set the voltage set ramprate (V/s) of the given channel"""
        log_info(self, "set_ramprate_voltage")
        ## WARNING discrepency unit between the get and set !
        value = value / 60
        self.send_cmd("CONF:RAMP:VOLT", channel=channel, arg=value)

    ## Not used in practice
    # def get_ramprate_current(self, channel):
    #     """Read the current set ramprate (A/s) of the given channel"""
    #     log_info(self, "get_ramprate_current")
    #     return self.send_cmd("READ:RAMP:CURR?", channel=channel)

    # def set_ramprate_current(self, channel):
    #     """Set the current set ramprate (A/s) of the given channel"""
    #     log_info(self, "set_ramprate_current")
    #     self.send_cmd("CONF:RAMP:CURR", channel=channel)

    def get_is_on(self, channel):
        """Read the enable state (ON) of the given channel"""
        log_info(self, "get_is_on")
        return self.send_cmd("READ:VOLT:ON?", channel=channel)

    def set_is_on(self, channel, value):
        """Switch the given channel with the set ramprate"""
        log_info(self, "set_is_on")
        self.send_cmd("VOLT", channel=channel, arg=value)

    def get_kill_enable(self):
        """Query the current value for the kill enable function"""
        log_info(self, "get_kill_enable")
        return self.send_cmd("CONF:KILL?")

    def set_kill_enable(self, value):
        """
        Set function kill enable (1) or kill disable (0).
        Factory default is Kill Disable.
        """
        log_info(self, "set_kill_enable")
        self.send_cmd("CONF:KILL", arg=value)

    def status_channel(self, channel):
        status = self.send_cmd("READ:CHAN:STAT?", channel=channel)
        status_dict = {}
        status_dict = {
            "is positive": bool(status & (1 << 0)),
            "is arc": bool(status & (1 << 1)),
            "is input error": bool(status & (1 << 2)),
            "is on": bool(status & (1 << 3)),
            "is voltage ramp": bool(status & (1 << 4)),
            "is emergency off": bool(status & (1 << 5)),
            "is constant current": bool(status & (1 << 6)),
            "is constant voltage": bool(status & (1 << 7)),
            "is low current range": bool(status & (1 << 8)),
            "is arc number exceeded": bool(status & (1 << 9)),
            "is current bounds": bool(status & (1 << 10)),
            "is voltage bounds": bool(status & (1 << 11)),
            "is external inhibit": bool(status & (1 << 12)),
            "is current trip": bool(status & (1 << 13)),
            "is current limit": bool(status & (1 << 14)),
            "is voltage limit": bool(status & (1 << 15)),
            "is current ramp": bool(status & (1 << 16)),
            "is current ramp up": bool(status & (1 << 17)),
            "is current ramp down": bool(status & (1 << 18)),
            "is voltage ramp up": bool(status & (1 << 19)),
            "is voltage ramp down": bool(status & (1 << 20)),
            "is voltage bound upper": bool(status & (1 << 21)),
            "is voltage bound lower": bool(status & (1 << 22)),
            "is flashover": bool(status & (1 << 26)),
            "is flashover nb exceeded": bool(status & (1 << 27)),
        }
        return status_dict

    def events_channel(self, channel):
        status = self.send_cmd("READ:CHAN:EV:STAT?", channel=channel)
        return {
            "event arc": bool(status & (1 << 1)),
            "event input_error": bool(status & (1 << 2)),
            "event on to off": bool(status & (1 << 3)),
            "event end of voltage_ramp": bool(status & (1 << 4)),
            "event emergency off": bool(status & (1 << 5)),
            "event constant current": bool(status & (1 << 6)),
            "event constant voltage": bool(status & (1 << 7)),
            "event arc number exceeded": bool(status & (1 << 9)),
            "event current bounds": bool(status & (1 << 10)),
            "event voltage bounds": bool(status & (1 << 11)),
            "event external inhibit": bool(status & (1 << 12)),
            "event current trip": bool(status & (1 << 13)),
            "event current limit": bool(status & (1 << 14)),
            "event voltage limit": bool(status & (1 << 15)),
            "event end of current ramp": bool(status & (1 << 16)),
            "event current ramp up": bool(status & (1 << 17)),
            "event current ramp down": bool(status & (1 << 18)),
            "event voltage ramp up": bool(status & (1 << 19)),
            "event voltage ramp down": bool(status & (1 << 20)),
            "event voltage bound upper": bool(status & (1 << 21)),
            "event voltage bound lower": bool(status & (1 << 22)),
            "event flashover": bool(status & (1 << 26)),
            "event flashover nb exceeded": bool(status & (1 << 27)),
        }

    def events_clear_channel(self, channel):
        self.send_cmd("EV", channel=channel, arg="CLEAR")

    def send_cmd(self, command, channel=None, arg=None):
        """Send a command to the controller.

        Arguments:
            command (str): The command string
            arg: Value to set

        Return:
            Answer from the controller if ? in the command
        """
        with self._lock:
            eol = "\r\n"
            if channel is None:
                if arg is None:
                    cmd_check = command
                    cmd = f"{command}{eol}"
                else:
                    cmd_check = f"{command} {arg}"
                    cmd = f"{cmd_check}{eol}"

                self.comm.write(cmd.encode())
                reply_cmd = (self.comm.readline(eol=eol)).decode("utf-8", "ignore")
                assert reply_cmd == cmd_check
                if "?" in command:
                    str_value = self.comm.readline(eol=eol).decode("utf-8", "ignore")
                    return str_value
            else:
                # Ask a return numerical value
                if arg is None:
                    cmd_check = f"{command} (@{channel})"
                    cmd = f"{cmd_check}{eol}"
                    self.comm.write(cmd.encode())
                    reply_cmd = (self.comm.readline(eol=eol)).decode("utf-8", "ignore")
                    assert reply_cmd == cmd_check
                    str_value = self.comm.readline(eol=eol).decode("utf-8", "ignore")
                    if "E" in str_value:
                        # remove unit/s
                        if str_value[-3].isalpha() and str_value[-3] != "E":
                            return float(str_value[:-3])
                        # remove unit
                        elif str_value[-1].isalpha():
                            return float(str_value[:-1])

                    else:
                        return int(str_value)
                # Set a value
                else:
                    cmd_check = f"{command} {arg},(@{channel})"
                    cmd = f"{cmd_check}{eol}"
                    self.comm.write(cmd.encode())
                    reply_cmd = (self.comm.readline(eol=eol)).decode("utf-8", "ignore")
                    assert reply_cmd == cmd_check
