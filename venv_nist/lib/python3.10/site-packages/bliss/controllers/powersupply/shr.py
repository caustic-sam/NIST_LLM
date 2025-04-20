# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


import enum
from functools import partial
import gevent

from bliss import global_map
from bliss.comm import scpi
from bliss.comm.scpi import Commands, COMMANDS
from bliss.comm.scpi import (
    Cmd,
    OnOffCmd,
    IntCmdRO,
    FloatCmd,
)
from bliss.comm.util import get_comm
from bliss.common.utils import autocomplete_property
from bliss.common.session import get_current_session

from bliss.common.counter import SamplingCounter
from bliss.controllers.counter import SamplingCounterController

from bliss.common.logtools import log_warning, log_info, log_debug
from bliss.common.soft_axis import SoftAxis
from bliss.common.axis.state import AxisState

from bliss.common.regulation import ExternalOutput

from bliss.common.protocols import IterableNamespace

from bliss.physics.units import ur, units

"""
SHR power supply, acessible via Ethernet / SCPI

yml configuration example:

- class: Shr
  module: powersupply.shr
  plugin: bliss
  name: shr
  tcp:
    url: id00-shr2220r:10001
    eol: "\r\n"
    timeout: 10

  counters:
    - counter_name: shr0_cv
      channel: A
      tag: voltage
      mode: SINGLE
    - counter_name: shr0_ci
      channel: A
      tag: current
      mode: SINGLE
    - counter_name: shr1_cv
      channel: B
      tag: voltage
      mode: SINGLE
    - counter_name: shr1_ci
      channel: B
      tag: current
      mode: SINGLE

  axes:
    - axis_name: shr0_hv
      channel: A
      tolerance: 10
      low_limit: 0
      high_limit: 250

    - axis_name: shr1_hv
      channel: B
      tolerance: 10
      low_limit: 0
      high_limit: 250
"""


"""
-   class: shrOutput
    module: powersupply.shr
    plugin: bliss
    name: shr_output_A
    device: $shr.chA
    unit: V
    #low_limit: 0.0
    #high_limit: 100.0
    ramprate: 10.0  #V/s

"""

QuantityCmd = partial(Cmd, get=ur.Quantity, set=ur.Quantity)
QuantityCmdRO = partial(Cmd, get=ur.Quantity)
QuantityCmdWO = partial(Cmd, set=ur.Quantity)

TemperatureCmdRO = partial(Cmd, get=lambda x: ur.Quantity(float(x[:-1]), ur.celsius))

SHR_COMMANDS = Commands(
    COMMANDS,
    {
        # conf commands
        "CONFigure:OUTPut[:MODE]": IntCmdRO(
            doc="Query the configured channel output mode"
        ),
        "CONFigure:RAMP[:VOLTage][:CURRent]": QuantityCmd(
            doc="Query the rampspeed config"
        ),
        "CONFigure:RAMP:VOLTage[:UP][:DOWN]": QuantityCmd(
            doc="Query the rampspeed voltage config"
        ),
        "CONFigure:RAMP:CURRent[:UP][:DOWN]": QuantityCmd(
            doc="Query the rampspeed voltage config"
        ),
        # -- read commands
        "READ:CURRent[:LIMit][:NOMinal][:BOUnds][:MODE]": QuantityCmdRO(
            doc="Query the channel current set value"
        ),
        "READ:VOLTage[:LIMit][:NOMinal][:BOUnds][:MODE]": QuantityCmdRO(
            doc="Query the channel voltage set value"
        ),
        "READ:RAMP:CURRent[:MIN][:MAX]": QuantityCmdRO(
            doc="Query the channel current ramp value"
        ),
        "READ:RAMP:VOLTage[:MIN][:MAX]": QuantityCmdRO(
            doc="Query the channel voltage ramp value"
        ),
        "READ:CURRent:ON": OnOffCmd(doc="Query the channel control bit Set On"),
        "READ:VOLTage:ON": OnOffCmd(doc="Query the channel control bit Set On"),
        "READ:CURRent:EMCY": OnOffCmd(
            doc="Query the channel control bit Set Emergency Off"
        ),
        "READ:VOLTage:EMCY": OnOffCmd(
            doc="Query the channel control bit Set Emergency Off"
        ),
        "READ:CHANnel:CONTrol": IntCmdRO(doc="Query the Channel Control register"),
        "READ:CHANnel:STATus": IntCmdRO(doc="Query the Channel Status register"),
        "READ:CHANnel:EVent:STATus": IntCmdRO(
            doc="Query the Channel Event Status register"
        ),
        "READ:MOD:TEMP": TemperatureCmdRO(doc="Query the Module temperature"),
        # -- measurement commands
        "MEASure:CURRent": QuantityCmdRO(doc="queries the measured current"),
        "MEASure:VOLTage": QuantityCmdRO(doc="queries the measured voltage"),
        # -- configuration commands
        "VOLTage[:BOUnds]": QuantityCmdWO(doc="Set the channel voltage"),
        "CURRent[:BOUnds]": QuantityCmdWO(doc="Set the channel current"),
        # -- easyramp
        "[SOURce:]VOLTage:RAMP[:STATe]": OnOffCmd(
            doc="activate or deactivate ramp function"
        ),
        "[SOURce:]VOLTage:RAMP:DURation": FloatCmd(
            doc="sets the duration of the voltage ramp"
        ),
    },
)


class shrOutput(ExternalOutput):
    def __init__(self, name, config):
        super().__init__(config)
        self.mode = config.get("mode", "absolute")

    # ----------- BASE METHODS -----------------------------------------

    @property
    def ramprate(self):
        """Get ramprate (in output unit per second)"""

        log_debug(self, "ExternalOutput:get_ramprate")

        return self.device.ramprate

    @ramprate.setter
    def ramprate(self, value):
        """Set ramprate (in output unit per second)"""

        log_debug(self, "ExternalOutput:set_ramprate: %s" % (value))

        self.device.ramprate = value
        self._ramp.rate = value

    def is_ramping(self):
        """
        Get the ramping status.
        """

        log_debug(self, "ExternalOutput:is_ramping")

        return self.device.is_ramping

    def _start_ramping(self, value):
        """Start the ramping process to target_value"""

        log_debug(self, "ExternalOutput:_start_ramping %s" % value)

        self.device.setpoint = value

    def _stop_ramping(self):
        """Stop the ramping process"""

        log_debug(self, "ExternalOutput:_stop_ramping")
        self.device._stop_ramping()

    # ----------- METHODS THAT A CHILD CLASS MAY CUSTOMIZE ------------------

    def state(self):
        """Return the state of the output device"""

        log_debug(self, "ExternalOutput:state")

        return self.device.status

    def read(self):
        """Return the current value of the output device (in output unit)"""

        log_debug(self, "ExternalOutput:read")

        return self.device.voltage

    def _set_value(self, value):
        """Set the value for the output. Value is expressed in output unit"""

        log_debug(self, "ExternalOutput:_set_value %s" % value)

        self.device.setpoint = value


class ShrCC(SamplingCounterController):
    def __init__(self, name, shr):
        super().__init__(name)
        self.shr = shr

    def read_all(self, *counters):
        values = []
        for cnt in counters:
            values.append(self.shr._channels[cnt.channel]._read_counter(cnt.tag))
        return values

    def read(self, counter):
        return self.shr._channels[counter.channel]._read_counter(counter.tag)


class ShrChannel:
    @enum.unique
    class _POLARITY(enum.Enum):
        POSITIVE = "p"
        NEGATIVE = "n"

    def __init__(self, shr, channel):
        self._shr = shr
        self._channel = channel
        self._chan_num = self._shr._CHANNEL[channel].value

    def __info__(self):
        return (
            f"CHANNEL {self._chan_num}\n"
            f"Voltage  : {self.voltage}\n"
            f"Current  : {self.current}\n"
            f"Setpoint : {self.voltage_setpoint:<20} (limit {self._voltage_mode})\n"
            f"         : {self.current_setpoint:<20} (limit {self._current_mode})\n\n"
            f"STATUS\n{self.status.__info__()}\n\n"
            f"EVENTS\n{self.events.__info__()}"
        )

    def _read_counter(self, tag):
        """Read actual current or voltage
        Args:
          tag (str): Valid entries: ['voltage', 'current']

        Returns:
          (Quantity): Voltage or current value
        """
        log_info(self, "_read_counter")
        return getattr(self, tag)

    def _read_limit(self, tag):
        """Read the current or voltage limit
        Args:
          tag (str): Valid entries: ['voltage', 'current']

        Returns:
          (float): Voltage or current value
        """
        log_info(self, "_read_limit")
        return getattr(self, tag + "_limit")

    class Register:
        def __init__(
            self,
            positive=False,
            arc=False,
            input_error=False,
            on=False,
            voltage_ramp=False,
            emergency_off=False,
            constant_current=False,
            constant_voltage=False,
            low_current_range=False,
            arc_number_exceeded=False,
            current_bounds=False,
            voltage_bounds=False,
            external_inhibit=False,
            current_trip=False,
            current_limit=False,
            voltage_limit=False,
            current_ramp=False,
            current_ramp_up=False,
            current_ramp_down=False,
            voltage_ramp_up=False,
            voltage_ramp_down=False,
            voltage_bound_upper=False,
            voltage_bound_lower=False,
            flashover=False,
            flashover_nb_exceeded=False,
        ):
            self.positive = positive
            self.arc = arc
            self.input_error = input_error
            self.on = on
            self.voltage_ramp = voltage_ramp
            self.emergency_off = emergency_off
            self.constant_current = constant_current
            self.constant_voltage = constant_voltage
            self.low_current_range = low_current_range
            self.arc_number_exceeded = arc_number_exceeded
            self.current_bounds = current_bounds
            self.voltage_bounds = voltage_bounds
            self.external_inhibit = external_inhibit
            self.current_trip = current_trip
            self.current_limit = current_limit
            self.voltage_limit = voltage_limit
            self.current_ramp = current_ramp
            self.current_ramp_up = current_ramp_up
            self.current_ramp_down = current_ramp_down
            self.voltage_ramp_up = voltage_ramp_up
            self.voltage_ramp_down = voltage_ramp_down
            self.voltage_bound_upper = voltage_bound_upper
            self.voltage_bound_lower = voltage_bound_lower
            self.flashover = flashover
            self.flashover_nb_exceeded = flashover_nb_exceeded

    class Status(Register):
        def __info__(self):
            return (
                f"HV Switch       : {'ON' if self.on else 'OFF'}\n"
                f"Polarity        : {'Positive' if self.positive else 'Negative'}\n"
                f"LC Range        : {'YES' if self.low_current_range else 'NO'}\n"
                f"Current Ramping : {'YES' if self.current_ramp else 'NO'}\n"
                f"Voltage Ramping : {'YES' if self.voltage_ramp else 'NO'}\n"
                f"Current Limit   : {'YES' if self.current_limit else 'NO'}\n"
                f"Voltage Limit   : {'YES' if self.voltage_limit else 'NO'}"
            )

    class Event(Register):
        def __info__(self):
            res = []
            for attr, value in self.__dict__.items():
                if value:
                    res.append(f"{attr.replace('_',' ').title():<20}: SET")

            if len(res):
                res = "\n".join(res)
            else:
                res = "No event"

            return res

    @property
    def status(self):
        status = self._shr._language(f"READ:CHAN:STAT? (@{self._chan_num})")[0][1]
        return self.Status(
            positive=bool(status & (1 << 0)),
            arc=bool(status & (1 << 1)),
            input_error=bool(status & (1 << 2)),
            on=bool(status & (1 << 3)),
            voltage_ramp=bool(status & (1 << 4)),
            emergency_off=bool(status & (1 << 5)),
            constant_current=bool(status & (1 << 6)),
            constant_voltage=bool(status & (1 << 7)),
            low_current_range=bool(status & (1 << 8)),
            arc_number_exceeded=bool(status & (1 << 9)),
            current_bounds=bool(status & (1 << 10)),
            voltage_bounds=bool(status & (1 << 11)),
            external_inhibit=bool(status & (1 << 12)),
            current_trip=bool(status & (1 << 13)),
            current_limit=bool(status & (1 << 14)),
            voltage_limit=bool(status & (1 << 15)),
            current_ramp=bool(status & (1 << 16)),
            current_ramp_up=bool(status & (1 << 17)),
            current_ramp_down=bool(status & (1 << 18)),
            voltage_ramp_up=bool(status & (1 << 19)),
            voltage_ramp_down=bool(status & (1 << 20)),
            voltage_bound_upper=bool(status & (1 << 21)),
            voltage_bound_lower=bool(status & (1 << 22)),
            flashover=bool(status & (1 << 26)),
            flashover_nb_exceeded=bool(status & (1 << 27)),
        )

    @property
    def events(self):
        status = self._shr._language(f"READ:CHAN:EV:STAT? (@{self._chan_num})")[0][1]
        return self.Event(
            arc=bool(status & (1 << 1)),
            input_error=bool(status & (1 << 2)),
            on=bool(status & (1 << 3)),
            voltage_ramp=bool(status & (1 << 4)),
            emergency_off=bool(status & (1 << 5)),
            constant_current=bool(status & (1 << 6)),
            constant_voltage=bool(status & (1 << 7)),
            arc_number_exceeded=bool(status & (1 << 9)),
            current_bounds=bool(status & (1 << 10)),
            voltage_bounds=bool(status & (1 << 11)),
            external_inhibit=bool(status & (1 << 12)),
            current_trip=bool(status & (1 << 13)),
            current_limit=bool(status & (1 << 14)),
            voltage_limit=bool(status & (1 << 15)),
            current_ramp=bool(status & (1 << 16)),
            current_ramp_up=bool(status & (1 << 17)),
            current_ramp_down=bool(status & (1 << 18)),
            voltage_ramp_up=bool(status & (1 << 19)),
            voltage_ramp_down=bool(status & (1 << 20)),
            voltage_bound_upper=bool(status & (1 << 21)),
            voltage_bound_lower=bool(status & (1 << 22)),
            flashover=bool(status & (1 << 26)),
            flashover_nb_exceeded=bool(status & (1 << 27)),
        )

    @property
    def polarity(self):
        return self._shr._language(f"CONF:OUTP:POL? (@{self._chan_num})")[0][1]

    @polarity.setter
    def polarity(self, value):
        return self._shr._language(f"CONF:OUTP:POL {value},(@{self._chan_num})")

    @property
    @units(result=ur.V)
    def voltage(self):
        return self._shr._language(f"MEAS:VOLT? (@{self._chan_num})")[0][1]

    @property
    @units(result=ur.A)
    def current(self):
        return self._shr._language(f"MEAS:CURR? (@{self._chan_num})")[0][1]

    @property
    @units(result=ur.V)
    def _voltage_mode(self):
        return self._shr._language(f"READ:VOLT:MODE? (@{self._chan_num})")[0][1]

    @property
    @units(result=ur.A)
    def _current_mode(self):
        return self._shr._language(f"READ:CURR:MODE? (@{self._chan_num})")[0][1]

    @property
    def _mode(self):
        return self._shr._language(f"CONF:OUTP:MODE? (@{self._chan_num})")[0][1]

    @property
    def _mode_list(self):
        if not hasattr(self, "_mode_list_cache"):
            res = self._shr._language(f"CONF:OUTP:MODE:LIST? (@{self._chan_num})")
            self._mode_list_cache = [int(m) for m in res[0][1].split(",")]

        return self._mode_list_cache

    @property
    def mode(self):
        return f"MODE {self._mode} : {self._voltage_mode} - {self._current_mode}"

    @mode.setter
    def mode(self, mode: int):
        if self.status.on:
            raise RuntimeError("Mode cannot be changed while channel is ON")
        if mode not in self._mode_list:
            raise ValueError(f"Invalid mode value {self._mode_list}")

        self._shr._language(f"CONF:OUTP:MODE {mode},(@{self._chan_num})")

    # @property
    # @units(result=ur.V)
    # def voltage_nominal(self):
    #     """read the nominal voltage of the given channel"""
    #     return self._shr._language(f"READ:VOLT:NOM? (@{self._chan_num})")[0][1]
    #
    # @property
    # @units(result=ur.A)
    # def current_nominal(self):
    #     """read the current voltage of the given channel"""
    #     return self._shr._language(f"READ:CURR:NOM? (@{self._chan_num})")[0][1]

    @property
    @units(result=ur.V)
    def voltage_bound(self):
        """read the voltage bound of the given channel"""
        return self._shr._language(f"READ:VOLT:BOU? (@{self._chan_num})")[0][1]

    @voltage_bound.setter
    @units(value=ur.V)
    def voltage_bound(self, value):
        """set the voltage bound of the given channel"""
        self._shr._language(f"VOLT:BOU {value.magnitude:.3f},(@{self._chan_num})")

    @property
    @units(result=ur.A)
    def current_bound(self):
        """read the current bound of the given channel"""
        return self._shr._language(f"READ:CURR:BOU? (@{self._chan_num})")[0][1]

    @current_bound.setter
    @units(value=ur.A)
    def current_bound(self, value):
        """set the current bound of the given channel"""
        self._shr._language(f"CURR:BOU {value.magnitude:.3f},(@{self._chan_num})")

    @property
    @units(result=ur.V)
    def voltage_setpoint(self):
        """read the voltage setpoint of the given channel"""
        return self._shr._language(f"READ:VOLT? (@{self._chan_num})")[0][1]

    @voltage_setpoint.setter
    @units(value=ur.V)
    def voltage_setpoint(self, value):
        """set the voltage setpoint of the given channel"""
        self._shr._language(f"VOLT {value.magnitude:.3f},(@{self._chan_num})")

    @property
    @units(result=ur.A)
    def current_setpoint(self):
        """read the current setpoint of the given channel"""
        return self._shr._language(f"READ:CURR? (@{self._chan_num})")[0][1]

    @current_setpoint.setter
    @units(value=ur.A)
    def current_setpoint(self, value):
        """set the current setpoint of the given channel"""
        self._shr._language(f"CURR {value.magnitude:.3f},(@{self._chan_num})")

    @property
    def voltage_on(self):
        """read the current setpoint of the given channel"""
        return self._shr._language(f"READ:VOLT:ON? (@{self._chan_num})")[0][1]

    @voltage_on.setter
    def voltage_on(self, value):
        """set the current setpoint of the given channel"""
        return self._shr._language(
            f"VOLT {'ON' if value else 'OFF'},(@{self._chan_num})"
        )

    @property
    def emergency(self):
        """read the emergency status of the given channel"""
        return self._shr._language(f"READ:VOLT:EMCY? (@{self._chan_num})")[0][1]

    # def emergency_off(self):
    #     """shut down the channel High Voltage (without ramp)."""
    #     self._shr._language(f"VOLT EMCY OFF,(@{self._chan_num})")

    def emergency_clear(self):
        """clear the emergency state of the given channel"""
        self._shr._language(f"VOLT EMCY CLR,(@{self._chan_num})")

    # @property
    # @units(result=ur.V / ur.s)
    # def voltage_ramprate(self):
    #     """read the voltage ramprate (V/s) of the given channel"""
    #     return self._shr._language(f"CONF:RAMP:VOLT? (@{self._chan_num})")[0][1]
    #
    # @voltage_ramprate.setter
    # @units(value=ur.V / ur.s)
    # def voltage_ramprate(self, value):
    #     """set the voltage (up and down) ramprate (V/s) of the given channel"""
    #     return self._shr._language(f"CONF:RAMP:VOLT {value.magnitude},(@{self._chan_num})")
    #
    # @property
    # @units(result=ur.A / ur.s)
    # def current_ramprate(self):
    #     """read the voltage ramprate (A/s) of the given channel"""
    #     return self._shr._language(f"CONF:RAMP:CURR? (@{self._chan_num})")[0][1]
    #
    # @current_ramprate.setter
    # @units(value=ur.A / ur.s)
    # def current_ramprate(self, value):
    #     """set the current (up and down) ramprate (A/s) of the given channel"""
    #     self._shr._language(f"CONF:RAMP:CURR {value.magnitude},(@{self._chan_num})")

    @property
    @units(result=ur.V / ur.s)
    def voltage_ramprate_up(self):
        """query the voltage ramprate up (V/s) of the given channel"""
        return self._shr._language(f"CONF:RAMP:VOLT:UP? (@{self._chan_num})")[0][1]

    @voltage_ramprate_up.setter
    @units(value=ur.V / ur.s)
    def voltage_ramprate_up(self, value):
        """set the voltage ramprate up (V/s) of the given channel"""
        self._shr._language(f"CONF:RAMP:VOLT:UP {value.magnitude},(@{self._chan_num})")

    @property
    @units(result=ur.V / ur.s)
    def voltage_ramprate_down(self):
        """query the voltage ramprate down (V/s) of the given devicechannel"""
        return self._shr._language(f"CONF:RAMP:VOLT:DOWN? (@{self._chan_num})")[0][1]

    @voltage_ramprate_down.setter
    @units(value=ur.V / ur.s)
    def voltage_ramprate_down(self, value):
        """set the voltage ramprate down (V/s) of the given channel"""
        self._shr._language(
            f"CONF:RAMP:VOLT:DOWN {value.magnitude},(@{self._chan_num})"
        )

    @property
    @units(result=ur.A / ur.s)
    def current_ramprate_up(self):
        """query the current ramprate up (A/s) of the given channel"""
        return self._shr._language(f"CONF:RAMP:CURR:UP? (@{self._chan_num})")[0][1]

    @current_ramprate_up.setter
    @units(value=ur.A / ur.s)
    def current_ramprate_up(self, value):
        """set the current ramprate up (A/s) of the given channel"""
        self._shr._language(f"CONF:RAMP:CURR:UP {value.magnitude},(@{self._chan_num})")

    @property
    @units(result=ur.A / ur.s)
    def current_ramprate_down(self):
        """query the current ramprate down (A/s) of the given channel"""
        return self._shr._language(f"CONF:RAMP:CURR:DOWN? (@{self._chan_num})")[0][1]

    @current_ramprate_down.setter
    @units(value=ur.A / ur.s)
    def current_ramprate_down(self, value):
        """set the current ramprate down (A/s) of the given channel"""
        self._shr._language(
            f"CONF:RAMP:CURR:DOWN {value.magnitude},(@{self._chan_num})"
        )

    @property
    def is_ramping(self):
        """returns True if currently ramping either voltage or current"""
        status = self.status
        return status.current_ramp or status.voltage_ramp

    def clear_events(self):
        """clear all events of the given channel"""
        self._shr._language(f"CONF:EV CLEAR,(@{self._chan_num})")


class Shr:
    @enum.unique
    class _CHANNEL(enum.IntEnum):
        none = -1
        A = 0
        B = 1

    def __init__(self, name, config):
        self._name = name
        self._config = config
        self._comm = get_comm(config)
        self._language = scpi.SCPI(
            interface=self._comm, commands=SHR_COMMANDS, cmd_ack=True
        )
        self._timeout = config.get("timeout", 3.0)
        self._comm_delay = 0.05
        self._lock = gevent.lock.Semaphore()

        self._unit_number = None
        self._software_version = None
        self._vout_max = None
        self._iout_max = None

        global_map.register(self, children_list=[self._comm])

        # --- ShrChannels ------
        self._channels = {"A": ShrChannel(self, "A"), "B": ShrChannel(self, "B")}

        # --- pseudo axes ------
        self._polling_time = 0.1
        self._axes_tolerance = {"A": None, "B": None}
        # self._axes_state = {"A": AxisState("READY"), "B": AxisState("READY")}
        self._soft_axes = {"A": None, "B": None}
        self._create_soft_axes(config)

        # ---- Counters -------------------------------------------------------------
        self._create_counters(config)

        self._comm.open()

    # def scan_metadata(self):
    #
    #     """
    #     this is about metadatscan_metadata publishing to the h5 file AND ICAT
    #     """
    #
    #     meta_dict = {"chA": self.chA.voltage}
    #     try:
    #         meta_dict.update({"chB": self.chB.voltage})
    #     except Exception:
    #         pass
    #
    #     meta_dict["@NX_class"] = "NXshr"
    #
    #     return meta_dict

    def __info__(self, level=1):
        info_list = []
        print(
            f"Gathering information from {self._config['tcp']['url']}, please wait few seconds...\n"
        )
        # Get full identification string
        idn = self.__idn
        info_list.append(
            f"=== Controller {idn['manufacturer']} {idn['model']} (sn{idn['serial']} fw ver{idn['version']}) ==="
        )
        info_list.append("")
        info_list.append(f"Mode ch0 : {self.ch0.mode}")
        info_list.append(f"Mode ch1 : {self.ch1.mode}")

        txt = "\n".join(info_list)
        return txt

    def __str__(self):
        # this is for the mapping: it needs a representation of instance
        return super().__repr__()

    @property
    def __idn(self):
        """Get the module identifier
        Returns: {manufacturer, model, serial, version}
        """
        return self._language["*IDN"]

    # @property
    # @units(result=ur.percent / ur.s)
    # def voltage_ramprate(self):
    #     """read the module voltage setpoint ramprate (%/s) for delayed trip / kill enabled"""
    #     return self._language["CONF:RAMP:VOLT"]
    #
    # @voltage_ramprate.setter
    # @units(result=ur.percent / ur.s)
    # def voltage_ramprate(self, value):
    #     """set the module voltage setpoint ramprate (%/s) for delayed trip / kill enabled"""
    #     self._language(f"CONF:RAMP:VOLT {value}")
    #
    # @property
    # @units(result=ur.percent / ur.s)
    # def current_ramprate(self):
    #     """read the module current setpoint ramprate (%/s) for delayed trip / kill enabled"""
    #     return self._language["CONF:RAMP:CURR"]
    #
    # @voltage_ramprate.setter
    # @units(value=ur.percent / ur.s)
    # def current_ramprate(self, value):
    #     """set the module current setpoint ramprate (%/s) for delayed trip / kill enabled"""
    #     self._language(f"CONF:RAMP:CURR {value}")

    @property
    @units(result=ur.celsius)
    def temperature(self):
        """read the module temperature (degC)"""
        return self._language["READ:MOD:TEMP"]

    @autocomplete_property
    def axes(self):
        return IterableNamespace(**{v.name: v for v in self._soft_axes.values()})

    # @autocomplete_property
    # def chA(self):
    #     return self._channels["A"]

    @autocomplete_property
    def ch0(self):
        return self._channels["A"]

    # @autocomplete_property
    # def chB(self):
    #     return self._channels["B"]

    @autocomplete_property
    def ch1(self):
        return self._channels["B"]

    @autocomplete_property
    def counters(self):
        """Standard counter namespace"""

        return self._cc.counters

    # ---- SOFT AXIS METHODS TO MAKE THE ACE SCANABLE -----------

    def _create_soft_axes(self, config):

        axes_conf = config.get("axes", [])

        for conf in axes_conf:

            name = conf["axis_name"].strip()
            chan = conf["channel"].strip().upper()

            low_limit = conf.get("low_limit")
            high_limit = conf.get("high_limit")

            tol = conf.get("tolerance")
            if tol:
                self._axes_tolerance[chan] = float(tol)

            if chan not in ["A", "B"]:
                raise ValueError(f"shr counter {name}: 'channel' must be in ['A', 'B']")

            self._soft_axes[chan] = SoftAxis(
                name,
                self,
                position=partial(self._axis_position, channel=chan),
                move=partial(self._axis_move, channel=chan),
                state=partial(self._axis_state, channel=chan),
                low_limit=low_limit,
                high_limit=high_limit,
                tolerance=self._axes_tolerance[chan],
                unit="V",
            )

    def _axis_position(self, channel=None):
        """Return the actual voltage of the given channel as the current position of the associated soft axis"""
        return self._channels[channel].voltage.magnitude

    def _axis_move(self, pos, channel=None):
        """Set the voltage setpoint to a new value as the target position of the associated soft axis"""
        ch = self._channels[channel]

        if not ch.voltage_on:
            raise ValueError(
                f"axis {self._soft_axes[channel].name} not ready (state={self._channels[channel].status})!"
            )
        ch.voltage_setpoint = pos

        log_debug(self, f"Moving axis to {pos}")

        # Wait for the PS to be ramping (or timeout)
        s = AxisState("READY")
        with gevent.Timeout(1.0, False):
            while s != AxisState("MOVING"):
                s = self._axis_state(channel)

        if s != AxisState("MOVING"):
            log_warning(self, "Moving axis timout (not ramping)")

        log_debug(self, f"Moving axis state {self._axis_state(channel)}")

    def _axis_state(self, channel=None):
        """Return the current state of the associated soft axis."""
        ch = self._channels[channel]

        # Standard axis states:
        # MOVING : 'Axis is moving'
        # READY  : 'Axis is ready to be moved (not moving ?)'
        # FAULT  : 'Error from controller'
        # LIMPOS : 'Hardware high limit active'
        # LIMNEG : 'Hardware low limit active'
        # HOME   : 'Home signal active'
        # OFF    : 'Axis is disabled (must be enabled to move (not ready ?))'

        if not ch.voltage_on:
            return AxisState("OFF")
        elif ch.is_ramping:
            return AxisState("MOVING")
        else:
            return AxisState("READY")

    # ---- COUNTERS METHODS ------------------------

    def _create_counters(self, config, export_to_session=True):

        cnts_conf = config.get("counters")
        if cnts_conf is None:
            return

        tag2unit = {
            "voltage": "V",
            "current": "A",
            "voltage_ramprate": "V/s",
            "current_ramprate": "A/s",
        }
        self._cc = ShrCC(self._name + "_counters_controller", self)
        self._cc.max_sampling_frequency = config.get("max_sampling_frequency", 1)

        for conf in cnts_conf:

            name = conf["counter_name"].strip()
            chan = conf["channel"].strip().upper()
            tag = conf["tag"].strip().lower()
            mode = conf.get("mode", "SINGLE")

            if chan not in ["A", "B"]:
                raise ValueError(f"shr counter {name}: 'channel' must be in ['A', 'B']")

            if tag not in [
                "voltage",
                "current",
                "voltage_ramprate",
                "current_ramprate",
            ]:
                raise ValueError(
                    f"shr counter {name}: 'tag' must be in ['voltage', 'current', 'voltage_ramprate', 'current_ramprate']"
                )

            cnt = self._cc.create_counter(
                SamplingCounter, name, unit=tag2unit[tag], mode=mode
            )
            cnt.channel = chan
            cnt.tag = tag

            if export_to_session:
                current_session = get_current_session()
                if current_session is not None:
                    if (
                        name in current_session.config.names_list
                        or name in current_session.env_dict.keys()
                    ):
                        raise ValueError(
                            f"Cannot export object to session with the name '{name}', name is already taken! "
                        )

                    current_session.env_dict[name] = cnt
