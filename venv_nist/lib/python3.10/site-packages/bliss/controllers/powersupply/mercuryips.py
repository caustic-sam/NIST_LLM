# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


import re
import gevent
import gevent.lock

from bliss import global_map
from bliss.comm.util import get_comm
from bliss.common.utils import autocomplete_property
from bliss.common.counter import SamplingCounter  # noqa: F401
from bliss.controllers.counter import SamplingCounterController
from bliss.common.logtools import log_info
from bliss.common.soft_axis import SoftAxis  # noqa: F401
from bliss.common.axis.state import AxisState
from bliss.controllers.bliss_controller import BlissController

"""
MercuryiPS power supply, acessible via ethernet

yml configuration example:

-  class: MercuryiPSController
   module: powersupply.mercuryips
   plugin: generic
   name: ipsox8tid12 # ulmag7tps
   tcp:
     url: ipsox8tid12.esrf.fr:7020 # ipsoxulmag7tid12.esrf.fr:7020

   counters:
     - name : set_point_field
       tag: set_point_field
     - name :  power_supply_field
       tag: power_supply_field
     - name : sweep_rate_field
       tag: ramp_rate
     - name: nitrogen_level
       tag: nitrogen_level
     - name : helium_level
       tag: helium_level
     - name: magnet_field
       tag: magnet_field

   field_axis:
     - name: field
       tolerance: 0.001
       steps_per_unit: 1
       max_field: 8
       ramp_rate: 3

   heater:
     - switching_time: 16

"""


class MercuryiPSCC(SamplingCounterController):
    def __init__(self, name, mercuryips):
        super().__init__(name)
        self.mercuryips = mercuryips

    def read(self, counter):
        return getattr(self.mercuryips, counter.tag)


class MercuryiPSController(BlissController):
    def __init__(self, config):
        super().__init__(config)
        self._hardware = MercuryiPSDevice(config)
        self._scc = MercuryiPSCC(self.name, self._hardware)
        global_map.register(self, parent_list=["controllers", "counters"])
        heater = self.hardware._heater
        if heater != "ON" and heater != "OFF":
            self._user_persistent_mode = None

        else:
            mode = "OFF" if heater == "ON" else "ON"
            self._user_persistent_mode = mode

        heater_list = self.config["heater"]
        self._heater_time = heater_list[0]["switching_time"]

        field_axis_list = self.config["field_axis"]
        self._ramp_rate = field_axis_list[0]["ramp_rate"]
        self.ramp_rate = self._ramp_rate

    def __info__(self, show_module_info=True):
        info_list = []
        print(f"\nGathering information from {self._config['tcp'].to_dict()}:\n")
        # Get full identification string
        identifier_dict = self._hardware.identification

        for key, value in identifier_dict.items():
            info_list.append(f"{key} = {value}")

        info_list.append("")
        status = self._hardware.status
        info_list.append(f"Status                   : {status}")
        activity = self._hardware.activity
        info_list.append(f"Activity                 : {activity}")
        info_list.append(f"Set point field          : {self._hardware.set_point_field}")
        info_list.append(
            f"Power supply field       : {self._hardware.power_supply_field}"
        )
        info_list.append(f"Ramp rate                : {self._hardware.ramp_rate}")
        info_list.append(f"Nitrogen level           : {self._hardware.nitrogen_level}")
        info_list.append(f"Helium level             : {self._hardware.helium_level}")
        info_list.append(f"Magnet field             : {self._hardware.magnet_field}")
        heater = self._hardware._heater
        info_list.append(f"Heater                   : {heater}")
        info_list.append(f"Persistent mode          : {self._user_persistent_mode}")

        return "\n".join(info_list)

    def _get_subitem_default_class_name(self, cfg, parent_key):
        if parent_key == "counters":
            return "SamplingCounter"

        elif parent_key == "field_axis":
            return "SoftAxis"

    def _create_subitem_from_config(
        self, name, cfg, parent_key, item_class, item_obj=None
    ):
        if parent_key == "counters":
            mode = cfg.get("mode", "MEAN")
            unit = cfg.get("unit", "")
            obj = self._scc.create_counter(item_class, name, mode=mode, unit=unit)
            obj.tag = cfg["tag"]
            return obj
        elif parent_key == "field_axis":
            return item_class(
                name,
                self,
                position=self._axis_position,
                move=self._axis_move,
                stop=self._axis_stop,
                state=self._axis_state,
                low_limit=-cfg.get("max_field", 1),
                high_limit=cfg.get("max_field", 1),
                tolerance=cfg.get("tolerance", 0.1),
                unit="T",
                as_positioner=True,
                export_to_session=False,
            )

    def _load_config(self):
        for cfg in self.config["counters"]:
            self._get_subitem(cfg["name"])

        self._field_axis = self._get_subitem(self.config["field_axis"][0]["name"])

    def _axis_position(self):
        """Return the actual field of the power supply as the current position of the associated soft axis"""
        return self.set_point_field

    def _axis_move(self, pos):
        """Set the target field of the power supply a new value of the associated soft axis"""

        persistent_mode = self._user_persistent_mode
        if persistent_mode != "ON" and persistent_mode != "OFF":
            raise ValueError(f"Persistent mode '{persistent_mode}' is not acceptable.")

        activity = self.activity
        if activity != self._hardware._RAMP2INFO["HOLD"]:
            raise ValueError(
                f"axis {self._field_axis.name} not ready. Activity is '{activity}'!"
            )

        if persistent_mode == "OFF":
            self.set_point_field = pos
            self.activity = self._hardware._RAMP2INFO["RTOS"]
        elif persistent_mode == "ON":
            self.persistent_mode = "off"
            self.set_point_field = pos
            self.activity = self._hardware._RAMP2INFO["RTOS"]
            while self.activity != self._hardware._RAMP2INFO["HOLD"]:
                gevent.sleep(1)

            # self.persistent_mode ='on'
            self._heater = "off"
            self.activity = self._hardware._RAMP2INFO["RTOZ"]

            self._user_persistent_mode = "ON"

    def _axis_stop(self):
        """Stop the motion of the associated soft axis"""
        self.activity = self._hardware._RAMP2INFO["HOLD"]

    def _axis_state(self):
        """Return the current state of the associated soft axis."""
        # Standard axis states:
        # MOVING : 'Axis is moving'
        # READY  : 'Axis is ready to be moved (not moving ?)'
        # FAULT  : 'Error from controller'
        # LIMPOS : 'Hardware high limit active'
        # LIMNEG : 'Hardware low limit active'
        # HOME   : 'Home signal active'
        # OFF    : 'Axis is disabled (must be enabled to move (not ready ?))'

        activity = self.activity
        if activity == self._hardware._RAMP2INFO["HOLD"]:
            return AxisState("READY")
        elif activity in [
            self._hardware._RAMP2INFO["RTOS"],
            self._hardware._RAMP2INFO["RTOZ"],
        ]:
            return AxisState("MOVING")
        elif activity != self._hardware._RAMP2INFO["CLMP"]:
            return AxisState("FAULT")
        return AxisState()

    @autocomplete_property
    def counters(self):
        return self._scc.counters

    @autocomplete_property
    def hardware(self):
        return self._hardware

    @property
    def identification(self):
        return self.hardware.identification

    @property
    def power_supply_field(self):
        """Field in T"""
        return self.hardware.power_supply_field

    @property
    def set_point_field(self):
        """Field in T"""
        return self.hardware.set_point_field

    @set_point_field.setter
    def set_point_field(self, value):
        """Target field in T"""
        self.hardware.set_point_field = value

    @property
    def ramp_rate(self):
        """Target ramp rate in T/min"""
        return self.hardware.ramp_rate

    @ramp_rate.setter
    def ramp_rate(self, value):
        """Target ramp rate in T/min"""
        self.hardware.ramp_rate = value

    @property
    def nitrogen_level(self):
        """Nitrogen sensor level in %"""
        return self.hardware.nitrogen_level

    @property
    def helium_level(self):
        """Helium sensor level in %"""
        return self.hardware.helium_level

    @property
    def magnet_field(self):
        """Persistent field in T"""
        return self.hardware.magnet_field

    @property
    def status(self):
        """Reads the alarm flags for the device (HexFormat)"""
        return self.hardware.status

    @property
    def activity(self):
        """Reads the Ramp status (Hold, to set, to zero, clamp)"""
        return self.hardware.activity

    @activity.setter
    def activity(self, value):
        """Set the Ramp status (Hold, to set, to zero, clamp)"""
        self.hardware.activity = value

    @property
    def _heater(self):
        """Reads the Switch heater status (check before set)"""
        heater = self.hardware._heater
        return heater

    @_heater.setter
    def _heater(self, value):
        """Set the Switch heater status (check before set)"""
        self.hardware._heater = (value, self._heater_time)

    @property
    def persistent_mode(self):
        """Reads the Switch heater status (check before set)"""
        heater = self.hardware._heater
        mode = "OFF" if heater == "ON" else "ON"
        self._user_persistent_mode = mode
        return mode

    @persistent_mode.setter
    def persistent_mode(self, value):
        """Set the Switch heater status (check before set)
        balance the power_supply_field with the magnet field
        """
        if value != "on" and value != "off":
            raise ValueError(f"Persistent mode '{value}' is not acceptable.")
        value = value.upper()
        heater_value = "OFF" if value == "ON" else "ON"
        magnet_field = self.hardware.magnet_field
        ## check activity before moving
        activity = self.activity
        if activity != self._hardware._RAMP2INFO["HOLD"]:
            raise ValueError(
                f"axis {self._field_axis.name} not ready. Activity is '{activity}'!"
            )
        self.set_point_field = magnet_field
        self.activity = self._hardware._RAMP2INFO["RTOS"]
        while self.activity != self._hardware._RAMP2INFO["HOLD"]:
            gevent.sleep(1)

        self._heater = heater_value
        if value == "ON":
            self._hardware.activity = self._hardware._RAMP2INFO["RTOZ"]
            while self.activity != self._hardware._RAMP2INFO["HOLD"]:
                gevent.sleep(1)

        self._user_persistent_mode = value


class MercuryiPSDevice:
    def __init__(self, config):
        self._config = config
        self._comm = None
        self._timeout = config.get("timeout", 3.0)
        self._lock = gevent.lock.Semaphore()
        self._RAMP2INFO = {
            "HOLD": "hold",
            "RTOS": "to set",
            "RTOZ": "to zero",
            "CLMP": "clamp",
        }
        self._INFO2RAMP = {
            "hold": "HOLD",
            "to set": "RTOS",
            "to zero": "RTOZ",
            "clamp": "CLMP",
        }
        self._STATUS2INFO = {
            "00000001": "Switch Heater Mismatch",
            "00000002": "Over Temperature [Rundown Resistors]",
            "00000004": "Over Temperature [Sense Resistor]",
            "00000008": "Over Temperature [PCB]",
            "00000010": "Calibration Failure",
            "00000020": "MSP430 Firmware Error",
            "00000040": "Rundown Resistors Failed",
            "00000080": "MSP430 RS-485 Failure",
            "00000100": "Quench detected",
            "00000200": "<undefined>",
            "00000400": "<undefined>",
            "00000800": "<undefined>",
            "00001000": "Over Temperature [Sense Amplifier]",
            "00002000": "Over Temperature [Amplifier 1]",
            "00004000": "Over Temperature [Amplifier 2]",
            "00008000": "PWM Cutoff",
            "00010000": "Voltage ADC error",
            "00020000": "Current ADC error",
            "00040000": "<undefined>",
            "00080000": "<undefined>",
            "00100000": "<undefined>",
            "00200000": "<undefined>",
            "00400000": "<undefined>",
            "00800000": "<undefined>",
            "01000000": "<undefined>",
            "02000000": "<undefined>",
            "04000000": "<undefined>",
            "08000000": "<reserved>",
            "10000000": "<reserved>",
            "20000000": "<reserved>",
            "40000000": "<reserved>",
            "80000000": "<reserved>",
        }
        catalog = (self.comm.write_readline(b"READ:SYS:CAT\n", eol="\n")).decode()
        catalog = catalog.split(":")
        index = catalog.index("LVL")
        self.level_daughter_board = catalog[index - 1]
        self.user_persistent_mode = "None"

    def __str__(self):
        # this is for the mapping: it needs a representation of instance
        return super().__repr__()

    @property
    def comm(self):
        if self._comm is None:
            self._comm = get_comm(self._config)
        return self._comm

    @property
    def identification(self):
        """Get the power supply identifier.

        Returns: {manufacturer, instrument_type, serial_number, firmware_version}
        """
        log_info(self, "identification_getter")
        info = self.send_cmd("IDN").split(":")
        manufacturer = f"{info[1]}"
        instrument_type = info[2]
        serial_number = info[3]
        firmware_version = info[4]
        return {
            "Manufacturer": manufacturer,
            "Instrument type": instrument_type,
            "Serial number": serial_number,
            "Firmware version": firmware_version,
        }

    @property
    def power_supply_field(self):
        """Field in T"""
        return float(self.send_cmd("READ:DEV:GRPZ:PSU:SIG:FLD"))

    @property
    def set_point_field(self):
        """Field in T"""
        return float(self.send_cmd("READ:DEV:GRPZ:PSU:SIG:FSET"))

    @set_point_field.setter
    def set_point_field(self, value):
        """Set the target value field in T"""
        self.send_cmd("SET:DEV:GRPZ:PSU:SIG:FSET", value)

    @property
    def ramp_rate(self):
        """Target ramp rate in T/min"""
        return float(self.send_cmd("READ:DEV:GRPZ:PSU:SIG:RFST"))

    @ramp_rate.setter
    def ramp_rate(self, value):
        """Set the value ramp rate in T/min"""
        self.send_cmd("SET:DEV:GRPZ:PSU:SIG:RFST", value)

    @property
    def nitrogen_level(self):
        """Nitrogen sensor level in %"""
        # return float(self.send_cmd("READ:DEV:DB5.L1:LVL:SIG:NIT:LEV")) # ulmag7tps
        #'READ:DEV:DB3.L1:LVL:SIG:NIT:LEV' # ipsox8tid12
        cmd = "READ:DEV:" + self.level_daughter_board + ":LVL:SIG:NIT:LEV"
        return float(self.send_cmd(cmd))

    @property
    def helium_level(self):
        """Helium sensor level in %"""
        # return float(self.send_cmd("READ:DEV:DB5.L1:LVL:SIG:HEL:LEV")) # ulmag7tps
        # return float(self.send_cmd("READ:DEV:DB3.L1:LVL:SIG:HEL:LEV")) # ipsox8tid12
        cmd = "READ:DEV:" + self.level_daughter_board + ":LVL:SIG:HEL:LEV"
        return float(self.send_cmd(cmd))

    @property
    def magnet_field(self):
        """Persistent field in T"""
        return float(self.send_cmd("READ:DEV:GRPZ:PSU:SIG:PFLD"))

    @property
    def status(self):
        """Reads the alarm flags for the device (HexFormat)"""
        status = self.send_cmd("READ:DEV:GRPZ:PSU:STAT")
        if status in self._STATUS2INFO.keys():
            return self._STATUS2INFO[status]
        else:
            return "not known"

    @property
    def activity(self):
        """Reads the Ramp status (Hold, to set, to zero, clamp)"""
        activity = self.send_cmd("READ:DEV:GRPZ:PSU:ACTN")
        return self._RAMP2INFO[activity]

    @activity.setter
    def activity(self, value):
        """Set the Ramp status (Hold, to set, to zero, clamp)"""
        value = self._INFO2RAMP[value]
        self.send_cmd("SET:DEV:GRPZ:PSU:ACTN", value)

    @property
    def _heater(self):
        """Reads the Switch heater status (check before set)"""
        heater = self.send_cmd("READ:DEV:GRPZ:PSU:SIG:SWHT")
        return heater

    @_heater.setter
    def _heater(self, value_tuple):
        """Set the Switch heater status (check before set)"""
        value, heater_time = value_tuple
        value = value.upper()
        heater_state = self.send_cmd("READ:DEV:GRPZ:PSU:SIG:SWHT")
        if value != heater_state:
            self.send_cmd("SET:DEV:GRPZ:PSU:SIG:SWHT", value, heater_time=heater_time)

    def send_cmd(self, command, arg=None, heater_time=None):
        """Send a command to the controller.

        Arguments:
            command (str): The command string
            args: Possible variable number of parameters

        Returns:
            Answer from the controller
        """

        log_info(self, "send_cmd")
        if command == "IDN":
            cmd = ("*" + command + "?\n").encode()
            asw = self.comm.write_readline(cmd, eol="\n")
            asw = asw.decode()
            return asw

        if arg is None:
            cmd = (command + "\n").encode()
            asw = self.comm.write_readline(cmd, eol="\n", timeout=1)
            asw = asw.decode()
            asw = asw.split(":")
            if asw[0] != "STAT":
                raise ValueError(f"Wrong answer '{asw[0]}' for the power supply.")

            if re.search("[0-9].[0-9]*", asw[-1]):
                if asw[-1].find("-") == -1:
                    asw = re.search("[0-9].[0-9]*", asw[-1]).group(0)
                else:
                    asw = re.search("[-][0-9].[0-9]*", asw[-1]).group(0)
            elif "ACTN" in asw or "SWHT" in asw:  # do not use SWHN
                asw = asw[-1]
            else:
                asw = "nan"
            return asw
        else:
            arg = str(arg)
            cmd = f"{command}:{arg}\n"
            cmd = cmd.encode()
            self.comm.write(cmd, timeout=1)
            if command.find("SWHT") == 21:
                ## time before reading ~0.3s is inclued in time switching
                ## time switching is 16s for the 8T
                ## time switching is 11s? for the 7T
                ## it is define in the yml of the power supply
                gevent.sleep(heater_time)

            asw = (self.comm.readline(eol="\n", timeout=1)).decode()
            asw = asw.split(":")
            if asw[-1] != "VALID":
                raise ValueError(
                    f"Argument '{arg}' is not 'VALID' because the answer is {asw[-1]}."
                )
