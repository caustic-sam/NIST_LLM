# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


"""
BLISS controller for Eurotherm temperature controller model 3504 and 3508.

YAML CONFIG EXAMPLE

- class: Eurotherm3000
  plugin: regulation
  module: temperature.eurotherm.eurotherm3000
  timeout: 3
  tcp:
    url: bm16eurohp.esrf.fr:502
  inputs:
    - name: eurohp_in
      unit: 'bar'

    - name: eurohp_in2
      channel: 2
      unit: 'bar'

  outputs:
    - name: eurohp_out
      unit: '%'
    - name: eurohp_out1
      channel: 1
      unit: '%'
    - name: eurohp_out2
      channel: 2
      unit: '%'

  ctrl_loops:
    - name: eurohp_loop
      input: $eurohp_in
      output: $eurohp_out

    - name: eurohp_loop2
      channel: 2
      input: $eurohp_in2
      output: $eurohp_out2
"""

import os.path
from ast import literal_eval

from bliss import global_map
from bliss.controllers.regulator import Controller
from bliss.controllers.regulator import Loop as RegulationLoop
from bliss.controllers.regulation.temperature.eurotherm.nanodac import (
    PropertiesMenuNode,
)
from bliss.common.regulation import lazy_init

from collections import namedtuple
from bliss.comm import modbus

from bliss.common.logtools import log_debug
from bliss.common.utils import autocomplete_property


def get_eurotherm_cmds():
    __fpath = os.path.realpath(__file__)
    __fdir = os.path.dirname(__fpath)
    fpath = os.path.join(__fdir, "eurotherm3000_cmds.txt")
    txt = open(fpath, "r").read()
    cmds = literal_eval(txt)

    return cmds


class Loop(RegulationLoop):
    @lazy_init
    def __info__(self):
        lines = ["\n"]
        lines.append(f"=== Loop: {self.name} ===")
        lines.append(
            f"controller: {self.controller.name if self.controller.name is not None else f'Eurotherm{self.controller.model}'} ({self.controller.state})"
        )
        lines.append(
            f"Input: {self.input.name} @ {self.input.read():.3f} {self.input.config.get('unit', 'N/A')}"
        )
        lines.append(
            f"output: {self.output.name} @ {self.output.read():.3f} {self.output.config.get('unit', 'N/A')}"
        )
        lines.append(f"Channel 1: output @ {self.ch1out} ")
        lines.append(f"Channel 2: output @ {self.ch2out} ")

        lines.append("\n=== Setpoint ===")
        lines.append(
            f"setpoint: {self.setpoint} {self.input.config.get('unit', 'N/A')}"
        )
        lines.append(f"ramprate: {self.ramprate}")
        lines.append(f"ramping: {self.is_ramping()}")
        lines.append("\n=== PID ===")
        lines.append(f"kp: {self.kp}")
        lines.append(f"ki: {self.ki}")
        lines.append(f"kd: {self.kd}")
        lines.append(f"kp2: {self.kp2}")
        lines.append(f"ki2: {self.ki2}")
        lines.append(f"kd2: {self.kd2}")

        return "\n".join(lines)

    @property
    @lazy_init
    def ch1out(self):
        """ """
        value = self._controller.get_ch1out(self)
        log_debug(self, f"{self.name} ch1out = {value}")
        return value

    @property
    @lazy_init
    def ch2out(self):
        """ """
        value = self._controller.get_ch2out(self)
        log_debug(self, f"{self.name} ch2out = {value}")
        return value

    @property
    @lazy_init
    def kp2(self):
        """ """
        value = self._controller.get_kp2(self)
        log_debug(self, f"{self.name} kp2 = {value}")
        return value

    @kp2.setter
    @lazy_init
    def kp2(self, value):
        """
        Set the P value (for PID2)
        """
        log_debug(self, f"{self.name} kp2 set to {value}")
        self._controller.set_kp2(self, value)

    @property
    @lazy_init
    def ki2(self):
        """
        Get the I value (for PID2)
        """
        value = self._controller.get_ki2(self)
        log_debug(self, f"{self.name} ki2 = {value}")
        return value

    @ki2.setter
    @lazy_init
    def ki2(self, value):
        """
        Set the I value (for PID2)
        """
        log_debug(self, f"{self.name} ki2 set to {value}")
        self._controller.set_ki2(self, value)

    @property
    @lazy_init
    def kd2(self):
        """
        Get the D value (for PID2)
        """
        value = self._controller.get_kd2(self)
        log_debug(self, f"{self.name} kd2 = {value}")
        return value

    @kd2.setter
    @lazy_init
    def kd2(self, value):
        """
        Set the D value (for PID2)
        """
        log_debug(self, f"{self.name} kd2 set to {value}")
        self._controller.set_kd2(self, value)

    @property
    @lazy_init
    def auto_manual(self):
        """
        Get the auto_manual
        False <=> auto
        True <=> manual
        """
        value = self._controller.get_auto_manual(self)
        rvalue = "Manual" if value == 1 else "Auto"
        log_debug(self, f"{self.name} auto_manual = {rvalue}")
        return rvalue

    # @auto_manual.setter
    # @lazy_init
    # def auto_manual(self, value):
    #     """
    #     Set the auto manual
    #     """
    #     log_debug(self, f"{self.name} auto_manual set to {value}")
    #     self._controller.set_auto_manual(self, value)


class Eurotherm3000(Controller):
    """
    Eurotherm3000 regulation controller.
    """

    def __init__(self, config):
        super().__init__(config)

        self._hw_controller = None
        self._setpoint = {}

    def __info__(self):
        return self.hw_controller.get_formatted_status()

    def dump_all_cmds(self):
        return self.hw_controller.dump_all_cmds()

    @autocomplete_property
    def hw_controller(self):
        if self._hw_controller is None:
            self._hw_controller = Eurotherm3000Device(self.config)
            self._hw_controller.initialize()
            global_map.register(self, children_list=[self._hw_controller])
        return self._hw_controller

    @autocomplete_property
    def cmds(self):
        return self.hw_controller.cmds

    @property
    def status(self):
        return self.hw_controller.status

    @property
    def state(self):
        # status = self.hw_controller.status
        # if (
        #     status.Heater_fail
        #     or status.Sensor_broken
        #     or status.PV_out_of_range
        #     or status.DC_control_module_fault
        # ):
        #     return "FAULT"
        # if status.Alarm_1 or status.Alarm_2 or status.Alarm_3 or status.Alarm_4:
        #     return "ALARM"
        # if not status.Ramp_program_complete:
        #     return "RUNNING"
        # return "READY"
        pass

    def show_status(self):
        self.hw_controller.show_status()

    @property
    def model(self):
        return self.hw_controller.model

    @property
    def auto_manual(self):
        return self.hw_controller.auto_manual

    # @auto_manual.setter
    # def auto_manual(self, value):
    #     self.hw_controller.auto_manual = value

    # ------ init methods ------------------------

    def initialize_controller(self):
        """
        Initializes the controller (including hardware).
        """
        self.hw_controller

    def initialize_input(self, tinput):
        """
        Initializes an Input class type object

        Args:
           tinput:  Input class type object
        """
        log_debug(self, "initialize_input")

    def initialize_output(self, toutput):
        """
        Initializes an Output class type object

        Args:
           toutput:  Output class type object
        """
        log_debug(self, "initialize_output")

    def initialize_loop(self, tloop):
        """
        Initializes a Loop class type object

        Args:
           tloop:  Loop class type object
        """
        if tloop.channel is None:
            tloop._channel = 1

        # Force input and output to share the same channel as their associated loop
        tloop.input._channel = tloop.channel
        tloop.output._channel = tloop.channel

    # ------ get methods ------------------------

    def read_input(self, tinput):
        """
        Reads an Input class type object
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tinput:  Input class type object

        Returns:
           read value  (in input unit)
        """
        log_debug(self, "Controller:read_input: %s" % (tinput))
        return self.hw_controller.send_cmd("process_variable", channel=tinput.channel)

    def read_output(self, toutput):
        """
        Reads an Output class type object
        Raises NotImplementedError if not defined by inheriting class

        Args:
           toutput:  Output class type object

        Returns:
           read value (in output unit)
        """
        log_debug(self, "Controller:read_output: %s" % (toutput))
        return self.hw_controller.send_cmd(
            "Loop.1.Main.ActiveOut", channel=toutput.channel
        )

    def state_input(self, tinput):
        """
        Return a string representing state of an Input object.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tinput:  Input class type object

        Returns:
           object state string. This is one of READY/RUNNING/ALARM/FAULT
        """
        log_debug(self, "Controller:state_input: %s" % (tinput))
        return self.state

    def state_output(self, toutput):
        """
        Return a string representing state of an Output object.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           toutput:  Output class type object

        Returns:
           object state string. This is one of READY/RUNNING/ALARM/FAULT
        """
        log_debug(self, "Controller:state_output: %s" % (toutput))
        return self.state

    # ------ PID methods ------------------------

    def set_kp(self, tloop, kp):
        """
        Set the PID P value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           kp: the kp value
        """
        log_debug(self, "Controller:set_kp: %s %s" % (tloop, kp))
        self.hw_controller.send_cmd(
            "loop.PID.ProportionalBand", kp, channel=tloop.channel
        )

    def get_kp(self, tloop):
        """
        Get the PID P value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           kp value
        """
        log_debug(self, "Controller:get_kp: %s" % (tloop))
        return self.hw_controller.send_cmd(
            "loop.PID.ProportionalBand", channel=tloop.channel
        )

    def set_ki(self, tloop, ki):
        """
        Set the PID I value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           ki: the ki value
        """
        log_debug(self, "Controller:set_ki: %s %s" % (tloop, ki))
        self.hw_controller.send_cmd("Loop.PID.IntegralTime", ki, channel=tloop.channel)

    def get_ki(self, tloop):
        """
        Get the PID I value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           ki value
        """
        log_debug(self, "Controller:get_ki: %s" % (tloop))
        return self.hw_controller.send_cmd(
            "Loop.PID.IntegralTime", channel=tloop.channel
        )

    def set_kd(self, tloop, kd):
        """
        Set the PID D value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           kd: the kd value
        """
        log_debug(self, "Controller:set_kd: %s %s" % (tloop, kd))
        self.hw_controller.send_cmd(
            "Loop.PID.DerivativeTime", kd, channel=tloop.channel
        )

    def get_kd(self, tloop):
        """
        Reads the PID D value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Output class type object

        Returns:
           kd value
        """
        log_debug(self, "Controller:get_kd: %s" % (tloop))
        return self.hw_controller.send_cmd(
            "Loop.PID.DerivativeTime", channel=tloop.channel
        )

    def get_ch1out(self, tloop):
        """
        Get the channel output
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           -100 < out value < 100
        """
        log_debug(self, "Controller:get_ch1out: %s" % (tloop))
        return self.hw_controller.send_cmd("Loop.1.OP.Ch1Out", channel=tloop.channel)

    def get_ch2out(self, tloop):
        """
        Get the channel output
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           -100 < out value < 100
        """
        log_debug(self, "Controller:get_ch2out: %s" % (tloop))
        return self.hw_controller.send_cmd("Loop.1.OP.Ch2Out", channel=tloop.channel)

    def set_kp2(self, tloop, kp2):
        """
        Set the PID P value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           kp: the kp value
        """
        log_debug(self, "Controller:set_kp2: %s %s" % (tloop, kp2))
        self.hw_controller.send_cmd(
            "loop.PID2.ProportionalBand", kp2, channel=tloop.channel
        )

    def get_kp2(self, tloop):
        """
        Get the PID P value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           kp value
        """
        log_debug(self, "Controller:get_kp2: %s" % (tloop))
        return self.hw_controller.send_cmd(
            "loop.PID2.ProportionalBand", channel=tloop.channel
        )

    def set_ki2(self, tloop, ki2):
        """
        Set the PID I value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           ki: the ki value
        """
        log_debug(self, "Controller:set_ki2: %s %s" % (tloop, ki2))
        self.hw_controller.send_cmd(
            "Loop.PID2.IntegralTime", ki2, channel=tloop.channel
        )

    def get_ki2(self, tloop):
        """
        Get the PID I value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           ki value
        """
        log_debug(self, "Controller:get_ki2: %s" % (tloop))
        return self.hw_controller.send_cmd(
            "Loop.PID2.IntegralTime", channel=tloop.channel
        )

    def set_kd2(self, tloop, kd2):
        """
        Set the PID D value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           kd: the kd value
        """
        log_debug(self, "Controller:set_kd2: %s %s" % (tloop, kd2))
        self.hw_controller.send_cmd(
            "Loop.PID2.DerivativeTime", kd2, channel=tloop.channel
        )

    def get_kd2(self, tloop):
        """
        Reads the PID D value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Output class type object

        Returns:
           kd value
        """
        log_debug(self, "Controller:get_kd: %s" % (tloop))
        return self.hw_controller.send_cmd(
            "Loop.PID2.DerivativeTime", channel=tloop.channel
        )

    # def set_auto_manual(self, tloop, auto_manual):
    #     """
    #     Set the PID D value
    #     Raises NotImplementedError if not defined by inheriting class

    #     Args:
    #        tloop:  Loop class type object
    #        kd: the kd value
    #     """
    #     log_debug(self, "Controller:set_auto_manual: %s %s" % (tloop, auto_manual))
    #     self.hw_controller.send_cmd("auto_man_select", auto_manual, channel=tloop.channel)

    def get_auto_manual(self, tloop):
        """
        Reads the PID D value
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Output class type object

           kd value
        """
        log_debug(self, "Controller:get_auto_manual: %s" % (tloop))
        return self.hw_controller.send_cmd("auto_man_select", channel=tloop.channel)

    def start_regulation(self, tloop):
        """varia
        Starts the regulation process.
        It must NOT start the ramp, use 'start_ramp' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
        """
        log_debug(self, "Controller:start_regulation: %s" % (tloop))
        pass

    def stop_regulation(self, tloop):
        """
        Stops the regulation process.
        It must NOT stop the ramp, use 'stop_ramp' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
        """
        log_debug(self, "Controller:stop_regulation: %s" % (tloop))
        pass

    # ------ setpoint methods ------------------------

    def set_setpoint(self, tloop, sp, **kwargs):
        """
        Set the current setpoint (target value).
        It must NOT start the PID process, use 'start_regulation' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
           sp:     setpoint (in tloop.input unit)
           **kwargs: auxilliary arguments
        """
        log_debug(self, "Controller:set_setpoint: %s %s" % (tloop, sp))
        pass

    def get_setpoint(self, tloop):
        """
        Get the current setpoint (target value)
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           (float) setpoint value (in tloop.input unit).
        """
        log_debug(self, "Controller:get_setpoint: %s" % (tloop))
        if self._setpoint.get(tloop.channel) is None:
            self._setpoint[tloop.channel] = self.hw_controller.send_cmd(
                "target_setpoint", channel=tloop.channel
            )
        return self._setpoint[tloop.channel]

    def get_working_setpoint(self, tloop):
        """
        Get the current working setpoint (setpoint along ramping)
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           (float) working setpoint value (in tloop.input unit).
        """
        log_debug(self, "Controller:get_working_setpoint: %s" % (tloop))
        return self.hw_controller.send_cmd("Loop.Main.WorkingSP", channel=tloop.channel)

    # ------ setpoint ramping methods (optional) ------------------------

    def start_ramp(self, tloop, sp, **kwargs):
        """
        Start ramping to a setpoint
        It must NOT start the PID process, use 'start_regulation' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Replace 'Raises NotImplementedError' by 'pass' if the controller has ramping but doesn't have a method to explicitly starts the ramping.
        Else if this function returns 'NotImplementedError', then the Loop 'tloop' will use a SoftRamp instead.

        Args:
           tloop:  Loop class type object
           sp:       setpoint (in tloop.input unit)
           **kwargs: auxilliary arguments
        """
        log_debug(self, "Controller:start_ramp: %s %s" % (tloop, sp))
        self.hw_controller.send_cmd("target_setpoint", sp, channel=tloop.channel)
        self._setpoint[tloop.channel] = sp

    def stop_ramp(self, tloop):
        """
        Stop the current ramping to a setpoint
        It must NOT stop the PID process, use 'stop_regulation' to do so.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object
        """
        log_debug(self, "Controller:stop_ramp: %s" % (tloop))
        sp = self.read_input(tloop.input)
        self.hw_controller.send_cmd("target_setpoint", sp, channel=tloop.channel)
        self._setpoint[tloop.channel] = sp

    def is_ramping(self, tloop):
        """
        Get the ramping status.
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           (bool) True if ramping, else False.
        """
        log_debug(self, "Controller:is_ramping: %s" % (tloop))

        wsp = self.hw_controller.send_cmd("Loop.Main.WorkingSP", channel=tloop.channel)
        sp = self.hw_controller.send_cmd("target_setpoint", channel=tloop.channel)

        if sp != wsp:
            return True
        else:
            return False

    def set_ramprate(self, tloop, rate):
        """Set the ramp rate

        Args:
           toutput (object): Output class type object
           rate (float): The ramp rate [degC/unit]
        """
        # self.hw_controller.send_cmd("Loop.SP.Rate", rate, channel=tloop.channel)
        raise NotImplementedError

    def get_ramprate(self, tloop):
        """
        Get the ramp rate
        Raises NotImplementedError if not defined by inheriting class

        Args:
           tloop:  Loop class type object

        Returns:
           ramp rate (in input unit per second)
        """
        log_debug(self, "Controller:get_ramprate: %s" % (tloop))
        return self.hw_controller.send_cmd("Loop.SP.Rate", channel=tloop.channel)

    # ------ raw commands ------------------------

    def WRraw(self, cmd):
        """
        Write then read the controller.

        Use white space to separate command and arguments.

        Args:
           cmd:  the command to write (str)
        returns:
           answer from the controller
        """
        log_debug(self, "Controller:WRraw:")

        args = cmd.strip().split()

        if len(args) > 2:
            channel = int(args[2])

            try:
                value = float(args[1])
            except ValueError:
                value = str(args[1])

            return self.hw_controller.send_cmd(args[0], value, channel)

        elif len(args) > 1:
            try:
                value = float(args[1])
            except ValueError:
                value = str(args[1])

            return self.hw_controller.send_cmd(args[0], value)

        elif len(args) > 0:
            return self.hw_controller.send_cmd(args[0])


class Eurotherm3000Device:
    RAMP_RATE_UNITS = ("sec", "min", "hour")
    SENSOR_TYPES = (
        "J",
        "K",
        "L",
        "R",
        "B",
        "N",
        "T",
        "S",
        "PL 2",
        "Custom (factory)",
        "RTD",
        "Linear mV (+/- 100mV)",
        "Linear V (0-10V)",
        "Linear mA",
        "Square root V",
        "Square root mA",
        "Custom mV",
        "Custom V",
        "Custom mA",
    )
    STATUS_FIELDS = (
        "Alarm_1",
        "Alarm_2",
        "Alarm_3",
        "Alarm_4",
        "Manual_mode",
        "Sensor_broken",
        "Open_loop",
        "Heater_fail",
        "Auto_tune_active",
        "Ramp_program_complete",
        "PV_out_of_range",
        "DC_control_module_fault",
        "Programmer_Segment_Sync_running",
        "Remote_input_sensor_broken",
    )
    EURO_STATUS = namedtuple("EURO_STATUS", STATUS_FIELDS)

    # load the default cmds dict => { cmd: (register, dtype), } with dtype in ['H', 'f', 'i']
    _DEFAULT_CMDS_MAPPING = get_eurotherm_cmds()

    def __init__(self, config):
        """
        TCP setting
        """

        self._model = None
        self._ident = None

        self._version = None

        host, port = config.get("tcp")["url"].split(":")
        self._host = host
        self._port = int(port)
        self._init_com()
        # host, port = config.get("tcp")["url"].split(":")
        # port = int(port)
        # self.comm = modbus.ModbusTcp(host=host, unit = 255, port = port)
        global_map.register(self, children_list=[self.comm])

    def initialize(self):
        """Get the model, the firmware version and the resolution of the module."""
        log_debug(self, "initialize")

        # get a copy of cmds dict in case it needs to be modified regarding the controller model
        # get the 3504 series cmds
        self._CMDS_MAPPING = self._DEFAULT_CMDS_MAPPING[3504].copy()

        self._read_identification()
        self._update_cmd_registers()
        # self._read_version()
        self._load_cmds()
        log_debug(
            self,
            # f"Eurotherm3000 {self._ident:02X} (firmware: {self._version:02X}) (comm: {self.comm!s})",
            f"Eurotherm3000 {self._ident:d} (comm: {self.comm!s})",
        )

    def _init_com(self):
        self.comm = modbus.ModbusTcp(host=self._host, unit=255, port=self._port)
        ## Flush the comm :'(
        # self._read_identification()

    def _read_identification(self):
        """
        For 3504/3508: Instrument ID (3508 = E480 / 3504 = HEX E440)
        0xE440 == 58432
        """
        ident = self.send_cmd("instrument_ident")
        # breakpoint()
        self._ident = int(ident)
        log_debug(self, "Connected to Eurotherm model ident code = %d" % self._ident)
        if self._ident == 58432:
            self._model = 3504
        log_debug(self, "Connected to Eurotherm model is %d" % self._model)

    def _update_cmd_registers(self):
        # The default cmds register values are for 2400 series
        # Update cmds register values for other models
        if self._model == 3504:
            pass

        elif self._model in self._DEFAULT_CMDS_MAPPING.keys():

            # for k in self._DEFAULT_CMDS_MAPPING[self._model].keys():
            #     print("update register {k} from {self._CMDS_MAPPING[k]} to {self._DEFAULT_CMDS_MAPPING[self._model][k]}")

            self._CMDS_MAPPING.update(self._DEFAULT_CMDS_MAPPING[self._model])

            # for k in self._DEFAULT_CMDS_MAPPING[self._model].keys():
            #     assert self._CMDS_MAPPING[k] == self._DEFAULT_CMDS_MAPPING[self._model][k]

        else:
            raise ValueError(f"Unsuported model {self._model} !")

    def _load_cmds(self):
        """Creates a PropertiesMenuNode to access all mapped commands as properties via self.cmds"""
        tmp = {}
        for k in self._CMDS_MAPPING:

            def getter_cb(obj, cmd=k):
                return self.send_cmd(cmd, None)

            def setter_cb(obj, value, cmd=k):
                return self.send_cmd(cmd, value)

            tmp[k] = (getter_cb, setter_cb)

        self.cmds = PropertiesMenuNode(tmp)

    ## TO DO
    # def _read_version(self):
    #     """
    #        In addition there are parameter addresses which change from
    #        controller to controller and software version to software version.
    #        Return the version number
    #     """

    #     self._version = self.send_cmd("Instrument.InstInfo.Version")
    #     log_debug(
    #         self,
    #         "Firmware V%x.%x"
    #         % ((self._version & 0xFF00) >> 8, (self._version & 0x00FF)),
    #     )

    def send_cmd(self, cmd, value=None, channel=None):
        """
        Send commands to the hardware controller.

        Args:
          ##cmd: a string or a tuple (register, dtype) with dtype in ['H', 'f', 'i']
          cmd: a string or a tuple (register, dtype, dp) with dtype in ['H', 'f'] and dp (decimal point).
               See 'self._CMDS_MAPPING' for all commands and associated register.
          value: if None, the register is read else it set to this value.
          channel: some commands can be sent to one of the 3 loops (2700 models).
                   If channel value is 2 the value of the register that will
                   be read is augmented by '(channel-1)*1024'.
        """
        if isinstance(cmd, (tuple, list)):
            reg, dtype, dp = cmd[0:3]
        else:
            reg, dtype, dp = self._CMDS_MAPPING[cmd]

        # Handle up to 2 loops
        if reg < 22000:
            if channel == 2:
                reg += (channel - 1) * 1024
        else:
            if channel == 2:
                reg += (channel - 1) * 269

        # print(f"reg  is {reg}, dtype is {dtype}, dp is {dp}, value is {value}")
        if value is None:
            log_debug(self, f"send_cmd {cmd} on channel {channel}")
            value = self.comm.read_holding_registers(reg, dtype)
            # print(f"value is {value}, ")

            if dp > 0:
                value = value / 10**dp
                log_debug(self, f"recv {value} from {cmd} on channel {channel}")
                return value
            else:
                log_debug(self, f"recv {value} from {cmd} on channel {channel}")
                return float("{:.2f}".format(value))

        else:
            if dtype == "f":
                value = float(value)
                log_debug(self, f"send_cmd {cmd} {value} on channel {channel}")
                self.comm.write_float(reg, value)

            else:
                self.comm.write_registers(reg, dtype, value)

    @property
    def model(self):
        return f"{self._ident:x}"

    @property
    def version(self):
        return self._version

    @property
    def sp(self):
        """Get target setpoint"""
        return self.send_cmd("target_setpoint")

    @sp.setter
    def sp(self, value):
        """Set target setpoint"""
        self.send_cmd("target_setpoint", value)

    @property
    def wsp(self):
        """Get working setpoint"""
        return self.send_cmd("Loop.Main.WorkingSP")

    @property
    def pv(self):
        """Get process variable"""
        return self.send_cmd("process_variable")

    @property
    def op(self):
        """Get output power"""
        return self.send_cmd("Loop.1.Main.ActiveOut")

    @property
    def ramprate(self):
        """Read the current ramprate.

        Returns:
          (float): current ramprate [degC/unit]
        """
        return self.send_cmd("Loop.SP.Rate")

    @ramprate.setter
    def ramprate(self, value):
        # raise NotImplementedError
        self.send_cmd("Loop.SP.Rate", value)

    # def get_ramprate_unit(self, channel=None):
    #     """Get the ramprate time unit.

    #     Returns:
    #       (str): Time unit - 'sec', 'min' or 'hour'
    #     """
    #     value = self.send_cmd("setpoint_rate_limit_units", channel=channel)
    #     return self.RAMP_RATE_UNITS[value]

    # def set_ramprate_unit(self, value, channel=None):
    #     """Set the ramprate time unit.

    #     Args:
    #       value (str): Time unit - 'sec', 'min' or 'hour'
    #     """
    #     if value not in self.RAMP_RATE_UNITS:
    #         raise ValueError(
    #             f"Invalid eurotherm ramp rate units. Should be in {self.RAMP_RATE_UNITS}"
    #         )

    # self.send_cmd("instrument_mode", 2)
    # self.send_cmd(
    #     "setpoint_rate_limit_units",
    #     self.RAMP_RATE_UNITS.index(value),
    #     channel=channel,
    # )
    # # self.send_cmd("instrument_mode", 0)

    @property
    def auto_manual(self):
        if self.send_cmd("auto_man_select") == 1:
            return True
        else:
            return False

    # @auto_manual.setter
    # def auto_manual(self, value):
    #     if value:
    #         self.send_cmd("auto_man_select", 1)
    #     else:
    #         self.send_cmd("auto_man_select", 0)

    @property
    def pid(self):
        return (self.kp, self.ki, self.kd)

    @property
    def kp(self):
        """Get proportional band PID"""
        return self.send_cmd("loop.PID.ProportionalBand")

    @kp.setter
    def kp(self, value):
        """Set proportional band PID"""
        self.send_cmd("loop.PID.ProportionalBand", value)

    @property
    def ki(self):
        """Get integral time PID"""
        return self.send_cmd("Loop.PID.IntegralTime")

    @ki.setter
    def ki(self, value):
        """Set integral time PID"""
        self.send_cmd("Loop.PID.IntegralTime", value)

    @property
    def kd(self):
        """Get derivative time PID"""
        return self.send_cmd("Loop.PID.DerivativeTime")

    @kd.setter
    def kd(self, value):
        """Set derivative time PID"""
        self.send_cmd("Loop.PID.DerivativeTime", value)

    @property
    def ch1out(self):
        """Get the output value from channel 1"""
        return self.send_cmd("Loop.1.OP.Ch1Out")

    @property
    def ch2out(self):
        """Get the output value from channel 1"""
        return self.send_cmd("Loop.1.OP.Ch2Out")

    @property
    def kp2(self):
        """Get proportional band PID2"""
        return self.send_cmd("loop.PID2.ProportionalBand")

    @kp2.setter
    def kp2(self, value):
        """Set proportional band PID2"""
        self.send_cmd("loop.PID2.ProportionalBand", value)

    @property
    def ki2(self):
        """Get integral time PID2"""
        return self.send_cmd("Loop.PID2.IntegralTime")

    @ki2.setter
    def ki2(self, value):
        """Set integral time PID2"""
        self.send_cmd("Loop.PID2.IntegralTime", value)

    @property
    def kd2(self):
        """Get derivative time PID2"""
        return self.send_cmd("Loop.PID2.DerivativeTime")

    @kd2.setter
    def kd2(self, value):
        """Set derivative time PID2"""
        self.send_cmd("Loop.PID2.DerivativeTime", value)

    @property
    def sensor_type(self):
        sensor = self.send_cmd("input_type")
        try:
            return self.SENSOR_TYPES[sensor]
        except IndexError:
            return f"Unknown sensor type:{sensor}"

    @sensor_type.setter
    def sensor_type(self, stype):
        if stype != self.sensor_type:
            # self.send_cmd("instrument_mode", 2)
            self.send_cmd("input_type", self.SENSOR_TYPES.index(stype))
            # self.send_cmd("instrument_mode", 0)

    def prog_status(self):
        """Read the setpoint status.

        Returns:
           (int): 0 - ready
                  1 - wsp != sp so running
                  2 - busy, a program is running
        """
        if self._model == 3504 and self.send_cmd("Programmer.Run.ProgStatus") == 2:
            return 2
        else:
            if self.wsp != self.sp:
                return 1
        return 0

    def is_ramping(self):
        return bool(self.prog_status())

    @property
    def status(self):
        # value = self.send_cmd("status_info")
        # status = self.EURO_STATUS(
        #     *[bool(value & (1 << i)) for i in range(len(self.EURO_STATUS._fields))]
        # )
        # return status
        pass

    # def get_formatted_status(self):
    #     status = self.status
    #     rows = [(field, str(getattr(status, field))) for field in status._fields]
    #     heads = ["EuroStatus", "Value"]
    #     return tabulate(rows, headers=heads)

    # def show_status(self):
    #     print(self.get_formatted_status())

    def dump_all_cmds(self):
        return {k: self.send_cmd(k) for k in self._CMDS_MAPPING.keys()}
