# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Bliss controller for ethernet PI E51X piezo controller.
Base controller for E517 and E518
"""

import time
import weakref

from bliss.controllers.motor import Controller
from bliss.controllers.counter import SamplingCounterController
from bliss.common.utils import object_method
from bliss.common.utils import object_attribute_get, object_attribute_set

from bliss.common.axis.state import AxisState
from bliss.common.logtools import log_info, log_debug

from bliss.common.event import connect, disconnect
from . import pi_gcs


# Special commands, e.g. fast polling commands, consist only of one
# character. The 24th ASCII character e.g. is called #24. Note that
# these commands are not followed by a termination character (but the
# responses to them are).
#
# * #5: Request Motion Status
# * #6: Query If Position Has Changed Since Last POS? Command
# * #7: Request Controller Ready Status
# * #8: Query If Macro Is Running
# * #9: Get Wave Generator Status
# * #24: Stop All Motion


class VoltageCounterController(SamplingCounterController):
    def __init__(self, pi_controller, pi_axis):
        super().__init__("voltage")

        self._controller = pi_controller
        self._axis = pi_axis

        # High frequency acquisition loop
        self.max_sampling_frequency = None

    def read(self, counter):
        return self._controller.get_output_voltage(self._axis)


class PI_E51X(pi_gcs.Communication, pi_gcs.Recorder, Controller):
    """
    Base class for E517 and E518
    """

    CHAN_LETTER = {1: "A", 2: "B", 3: "C"}

    model = None  # defined in inherited classes.

    def __init__(self, *args, **kwargs):
        # Called at session startup
        # No hardware access
        pi_gcs.Communication.__init__(self)
        pi_gcs.Recorder.__init__(self)
        Controller.__init__(self, *args, **kwargs)

        self._nb_channel = 3

        # Keep cache of: online, closed_loop, auto_gate, low_limit, high_limit
        # per axis (it cannot be global ones otherwise last axis initialized
        # is the only winner !!)
        # To be chganged for BLISS setting ?
        self._axis_online = weakref.WeakKeyDictionary()
        self._axis_closed_loop = weakref.WeakKeyDictionary()
        self._axis_auto_gate = weakref.WeakKeyDictionary()
        self._axis_low_limit = weakref.WeakKeyDictionary()
        self._axis_high_limit = weakref.WeakKeyDictionary()

    def _get_subitem_default_module(self, class_name, cfg, parent_key):
        if parent_key == "counters":
            return "bliss.common.counter"
        else:
            return Controller._get_subitem_default_module(
                self, class_name, cfg, parent_key
            )

    def _get_subitem_default_class_name(self, cfg, parent_key):
        if parent_key == "counters":
            return "SamplingCounter"
        else:
            return Controller._get_subitem_default_class_name(self, cfg, parent_key)

    def _create_subitem_from_config(
        self, name, cfg, parent_key, item_class, item_obj=None
    ):

        if parent_key == "counters":
            # cfg = {'name': 'pi2_v', 'tag': 'voltage', 'axis': <bliss.config.static.ConfigReference object at 0x7fbd591adfd0>}
            self._v_counter_controller = VoltageCounterController(self, cfg["axis"])
            counter = self._v_counter_controller.create_counter(
                item_class, name, conversion_function=None, unit=None
            )
            # self._counters[name] = counter
            return counter
        else:
            return Controller._create_subitem_from_config(
                self, name, cfg, parent_key, item_class, item_obj
            )

    def move_done_event_received(self, state, sender=None):
        """
        <sender> is the axis.
        """
        log_info(
            self,
            "move_done_event_received(state=%s axis.sender=%s)",
            state,
            sender.name,
        )
        if self._axis_auto_gate[sender]:
            if state is True:
                log_info(self, "PI_E51X.py : movement is finished")
                self.set_gate(sender, 0)
                log_debug(self, "mvt finished, gate set to 0")
            else:
                log_info(self, "PI_E51X.py : movement is starting")
                self.set_gate(sender, 1)
                log_debug(self, "mvt started, gate set to 1")

    def initialize(self):
        """
        Controller initialization.
        Called at session startup.
        Called Only once per controller even if more than
        one axis is declared in config.
        """

        # acceleration is not mandatory in config
        self.axis_settings.config_setting["acceleration"] = False

        # should have been set at init of inherited classes.
        log_debug(self, "model=%s", self.model)

    def close(self):
        """
        Called at session exit. 6 times ???
        """
        self.com_close()
        for axis in self.axes.values():
            disconnect(axis, "move_done", self.move_done_event_received)

    def initialize_hardware(self):
        """
        Called once per controller at first use of any of the axes.
        """
        # Initialize socket communication.
        self.com_initialize()

    def initialize_hardware_axis(self, axis):
        """
        Called once per axis at first use of the axis
        """
        pass

    def initialize_axis(self, axis):
        """
        Called at first access to <axis> (eg: __info__())

        - Reads specific config
        - Adds specific methods
        - Switches piezo to ONLINE mode so that axis motion can be caused
          by move commands.

        Args:
            - <axis>
        Returns:
            - None
        """
        # called at first p1 access (eg: __info__())

        axis.channel = axis.config.get("channel", int)
        if axis.channel not in (1, 2, 3):
            if self.model == "E517_i1":
                axis.channel = 1
            else:
                raise ValueError(
                    "PI_E51X invalid motor channel : can only be 1, 2 or 3"
                )
        axis.chan_letter = self.CHAN_LETTER[axis.channel]

        # set online
        self.set_on(axis)

        # set velocity control mode
        self._set_velocity_control_mode(axis)

        # Closed loop
        self._axis_closed_loop[axis] = self._get_closed_loop_status(axis)
        servo_mode = axis.config.get("servo_mode", bool, None)
        if servo_mode is not None:
            if self._axis_closed_loop[axis] != servo_mode:
                self._set_closed_loop(axis, servo_mode)

        # Drift compensation
        drift_mode = axis.config.get("drift_compensation", bool, None)
        if drift_mode is not None:
            self._set_dco(axis, int(drift_mode))

        # automatic gate (OFF by default)
        self._axis_auto_gate[axis] = False

        # connect move_done for auto_gate mode
        connect(axis, "move_done", self.move_done_event_received)

        # keep limits for gate
        self._axis_low_limit[axis] = self._get_low_limit(axis)
        self._axis_high_limit[axis] = self._get_high_limit(axis)

    def initialize_encoder(self, encoder):
        encoder.channel = encoder.config.get("channel", int)
        if encoder.channel not in (1, 2, 3):
            raise ValueError("PI_E51X invalid motor channel : can only be 1, 2 or 3")
        encoder.chan_letter = self.CHAN_LETTER[encoder.channel]

    def _set_velocity_control_mode(self, axis):
        self.command(f"VCO {axis.chan_letter} 1")

    def set_on(self, axis):
        log_debug(self, "set %s ONLINE" % axis.name)
        self.command(f"ONL {axis.channel} 1")
        self._axis_online[axis] = 1

    def set_off(self, axis):
        log_debug(self, "set %s OFFLINE" % axis.name)
        self.command(f"ONL {axis.channel} 0")
        self._axis_online[axis] = 0

    def read_position(
        self, axis, last_read={"t": time.perf_counter(), "pos": [None, None, None]}
    ):
        """
        Return position's setpoint for <axis>.
        Setpoint position is MOV? of SVA? depending on closed-loop
        mode is ON or OFF.

        Args:
            - <axis> : bliss axis.
        Returns:
            - <position> : float : piezo position in Micro-meters or in Volts.
        """
        cache = last_read

        if time.perf_counter() - cache["t"] < 0.005:
            _pos = cache["pos"]
            log_debug(self, "position setpoint cache : %r" % _pos)
        else:
            _pos = self._get_target_pos(axis)
            cache["pos"] = _pos
            cache["t"] = time.perf_counter()
            log_debug(self, "position setpoint read : %r" % _pos)

        return _pos[axis.channel - 1]

    def read_encoder(
        self, encoder, last_read={"t": time.perf_counter(), "pos": [None, None, None]}
    ):
        cache = last_read

        if time.perf_counter() - cache["t"] < 0.005:
            _pos = cache["pos"]
            log_debug(self, "position measured cache : %r" % _pos)
        else:
            _pos = self._get_pos()
            cache["pos"] = _pos
            cache["t"] = time.perf_counter()
            log_debug(self, "position measured read : %r" % _pos)

        return _pos[encoder.channel - 1]

    def read_velocity(self, axis):
        """
        Args:
            - <axis> : Bliss axis object.
        Returns:
            - <velocity> : float
        """
        _ans = self.command(f"VEL? {axis.chan_letter}")
        # _ans should looks like "A=+0012.0000"
        # removes 'X=' prefix
        _velocity = float(_ans)

        log_debug(self, "read %s velocity : %g " % (axis.name, _velocity))
        return _velocity

    def set_velocity(self, axis, new_velocity):
        self.command(f"VEL {axis.chan_letter} {new_velocity}")
        log_debug(self, "%s velocity set : %g" % (axis.name, new_velocity))
        return self.read_velocity(axis)

    def state(self, axis):
        # print(f"pi_e51x.py -- state({axis.name})")
        if not self._axis_online[axis]:
            # print("Axis is OFF")
            return AxisState("OFF")

        if self._axis_closed_loop[axis]:
            log_debug(self, "%s state: CLOSED-LOOP active" % axis.name)
            if self._get_on_target_status(axis):
                # print("ONT")
                return AxisState("READY")
            else:
                # print("not ONT")
                return AxisState("MOVING")
        else:
            log_debug(self, "%s state: CLOSED-LOOP is not active" % axis.name)
            return AxisState("READY")

    def prepare_move(self, motion):
        """
        - TODO for multiple move...
        """
        pass

    def start_one(self, motion):
        """
        - Sends 'MOV' or 'SVA' depending on closed loop mode.

        Args:
            - <motion> : Bliss motion object.

        Returns:
            - None
        """
        chan_letter = motion.axis.chan_letter
        tg_pos = motion.target_pos

        if self._axis_closed_loop[motion.axis]:
            # Command in position.
            log_debug(self, "Move %s in position to %g" % (motion.axis.name, tg_pos))
            self.command(f"MOV {chan_letter} {tg_pos}")
        else:
            # Command in voltage.
            log_debug(self, f"Move {motion.axis.name} in voltage to {tg_pos}")
            self.command(f"SVA {chan_letter} {tg_pos}")

    def stop(self, axis):
        """
        * HLT -> stop smoothly
        * STP -> stop asap
        * 24    -> stop asap
        * to check : copy of current position into target position ???
        """
        self.command("HLT %s" % axis.chan_letter)

    """
    E51X specific
    """

    def _get_pos(self):
        """
        Args:
            - <axis> :
        Returns:
            - <position>: real positions (POS? command) read by capacitive sensor.

        Raises:
            ?
        """
        _ans = self.command("POS?", nb_line=self._nb_channel)
        if self._nb_channel > 1:
            _pos = list(map(float, [x[2:] for x in _ans]))
        else:
            _pos = [float(_ans[2:])]

        return _pos

    def _get_target_pos(self, axis):
        """Return last targets positions for all 3 axes.
            - (MOV?/SVA? command) (setpoint value).
            - SVA? : Query the commanded output voltage (voltage setpoint).
            - MOV? : Return the last valid commanded target position.
        Args:
            - <>
        Return:
            - list of float
        """
        if self._axis_closed_loop[axis]:
            _ans = self.command("MOV?", nb_line=self._nb_channel)
        else:
            _ans = self.command("SVA?", nb_line=self._nb_channel)

        if self._nb_channel > 1:
            _pos = list(map(float, [x[2:] for x in _ans]))
        else:
            _pos = [float(_ans[2:])]

        return _pos

    def _get_low_limit(self, axis):
        _ans = self.command(f"NLM? {axis.chan_letter}")
        return float(_ans)

    def _get_high_limit(self, axis):
        _ans = self.command(f"PLM? {axis.chan_letter}")
        return float(_ans)

    """
    DCO : Drift Compensation Offset.
    """

    @object_method(types_info=("None", "None"))
    def activate_dco(self, axis):
        self._set_dco(axis, 1)

    @object_method(types_info=("None", "None"))
    def desactivate_dco(self, axis):
        self._set_dco(axis, 0)

    @object_attribute_get(type_info="bool")
    def get_dco(self, axis):
        _dco = self.command(f"DCO? {axis.chan_letter}")
        val = bool(int(_dco))
        return val

    @object_method(types_info=("bool", "None"))
    def set_dco(self, axis, onoff):
        log_debug(self, "set drift compensation (dco) to %s" % onoff)
        self._set_dco(axis, onoff)

    def _set_dco(self, axis, onoff):
        self.command(f"DCO {axis.chan_letter}, {onoff}")

    def _get_cto(self, axis):
        _cto_ans = self.command("CTO?", nb_line=24)
        return _cto_ans

    """
    Voltage commands
    """

    @object_method(types_info=("None", "float"))
    def get_voltage(self, axis):
        """
        Returns Voltage Of Output Signal Channel (SVA? command)
        """
        _ans = self.command(f"SVA? {axis.chan_letter}")
        _vol = float(_ans)
        return _vol

    @object_method(types_info=("None", "float"))
    def get_output_voltage(self, axis):
        """
        Return output voltage
        """
        channel, value = self.command("VOL?").split("=")
        if int(channel) == axis.channel:
            return float(value)
        else:
            raise RuntimeError(
                "cannot read output voltage for {axis.name} channel {axis.channel} - returned {channel} and {value}."
            )

    # Voltage Low  Hard-Limit (ID 0x0B000007)
    # Voltage High Hard-Limit (ID 0x0B000008)
    # ("Voltage output high limit  ", "VMA? %s" % axis.channel),
    # ("Voltage output low limit   ", "VMI? %s" % axis.channel),

    @object_method(types_info=("None", "float"))
    def get_voltage_high_limit(self, axis):
        """
        Return voltage HIGH limit
        """
        return float(self.command(f"VMA? {axis.channel}"))

    @object_method(types_info=("None", "float"))
    def get_voltage_low_limit(self, axis):
        """
        Return voltage LOW limit
        """
        return float(self.command(f"VMI? {axis.channel}"))

    """
    Closed loop commands
    """

    def _get_closed_loop_status(self, axis):
        """
        Returns Closed loop status (Servo state) (SVO? command)
        -> True/False
        """
        _ans = self.command(f"SVO? {axis.chan_letter}")
        _status = bool(int(_ans))
        return _status

    def _set_closed_loop(self, axis, onoff):
        log_debug(self, "set %s closed_loop to %s" % (axis.name, onoff))
        self.command(f"SVO {axis.chan_letter} {onoff}")
        self._axis_closed_loop[axis] = self._get_closed_loop_status(axis)
        log_debug(
            self, "effective closed_loop is now %s" % self._axis_closed_loop[axis]
        )
        if self._axis_closed_loop[axis] != onoff:
            raise RuntimeError(
                "Failed to change %s closed_loop mode to %s (check servo switch ?)"
                % (axis.name, onoff)
            )

    def _get_on_target_status(self, axis):
        """
        Returns << On Target >> status (ONT? command).
        True/False
        """
        _ans = self.command(f"ONT? {axis.chan_letter}")
        _status = bool(int(_ans))
        return _status

    @object_method(types_info=("None", "None"))
    def open_loop(self, axis):
        self._set_closed_loop(axis, 0)

    @object_method(types_info=("None", "None"))
    def close_loop(self, axis):
        self._set_closed_loop(axis, 1)

    @object_method(types_info=("bool", "None"))
    def set_closed_loop(self, axis, onoff):
        self._set_closed_loop(axis, onoff)

    @object_attribute_get(type_info="bool")
    def get_closed_loop(self, axis):
        return self._axis_closed_loop[axis]

    """
    Auto gate
    """

    @object_attribute_get(type_info="bool")
    def get_auto_gate(self, axis):
        """Automatic gating for continuous scan"""
        return self._axis_auto_gate[axis]

    @object_attribute_set(type_info="bool")
    def set_auto_gate(self, axis, value):
        self._axis_auto_gate[axis] = value is True
        log_info(
            self,
            "auto_gate is %s for axis.channel %s",
            value is True and "ON" or "OFF",
            axis.channel,
        )

    @object_method(types_info=("bool", "None"))
    def set_gate(self, axis, state):
        """
        De/Activate digital output on controller.
        """
        raise NotImplementedError

    """
    ID/INFO
    """

    @object_attribute_get(type_info="str")
    def get_model(self, axis):
        return self.model

    def __info__(self, axis=None):
        if axis is None:
            return self.get_controller_info()
        else:
            return self.get_info(axis)

    def get_controller_info(self):
        _infos = [
            ("Identifier ", "*IDN?"),
            # ("Serial Number ", "SSN?"),  # useless for end-user.
            # ("Com level ", "CCL?"),
            # ("GCS Syntax version ", "CSV?"),
            # ("Last error code ", "ERR?"),
        ]

        _txt = f"PI_{self.model} controller :\n"
        # Reads pre-defined infos (1 line answers)
        for label, cmd in _infos:
            value = self.command(cmd)
            _txt += "     %s %s\n" % (label, value)

        _txt += "     Communication parameters: \n"

        if self.model.startswith("E517"):
            # Communication parameters.
            _txt = _txt + "       %s\n" % (
                "\n       ".join(self.command("IFC?", nb_line=6)),
            )

            # Firmware version
            _txt = _txt + "     %s  \n       %s\n" % (
                "Firmware versions",
                "\n       ".join(self.command("VER?", nb_line=3)),
            )

        if self.model == "E518":
            # Communication parameters.
            _txt = _txt + "       %s\n" % (
                "\n       ".join(self.command("IFC?", nb_line=5)),
            )

            # Firmware version
            _txt = _txt + "     %s  \n       %s\n" % (
                "Firmware versions",
                "\n       ".join(self.command("VER?", nb_line=5)),
            )

        return _txt

    def get_id(self, axis):
        """
        Return:
            str: Identification string.
                 like: '(c) 2016 Physik Instrumente (PI) GmbH & Co. KG, E-518.I3, 120057304, 3.70'
        """
        return self.command("*IDN?")

    def get_axis_info(self, axis):
        """
        Return Controller specific info about <axis>
        """
        info_str = "PI AXIS INFO:\n"
        info_str += f"     voltage (SVA) = {self.get_voltage(axis)}\n"
        info_str += f"     output voltage (VOL) = {self.get_output_voltage(axis)}\n"
        info_str += f"     closed loop = {self.get_closed_loop(axis)}\n"
        _ont_win = float(self.command(f"SPA? {axis.channel} 0x7000900"))
        info_str += f"     on-target window = {_ont_win}\n"

        if self.model.startswith("E517"):
            # No settling time in doc for 517.
            pass
        else:
            _ont_settling_time = float(self.command(f"SPA? {axis.channel} 0x7000901"))
            info_str += f"     on-target settling time = {_ont_settling_time}\n"

        return info_str

    @object_method(types_info=("None", "string"))
    def get_info(self, axis):
        """
        Return a set of information about controller.
        Helpful to tune the device.

        Args:
            <axis> : bliss axis
        Returns:
            None
        """
        _infos = [
            ("Real Position              ", "POS? %s" % axis.chan_letter),
            ("Position low limit         ", "NLM? %s" % axis.chan_letter),
            ("Position high limit        ", "PLM? %s" % axis.chan_letter),
            ("Closed loop status         ", "SVO? %s" % axis.chan_letter),
            ("Voltage output high limit  ", "VMA? %s" % axis.channel),
            ("Voltage output low limit   ", "VMI? %s" % axis.channel),
            ("Output Voltage             ", "VOL? %s" % axis.channel),
            ("Setpoint Position          ", "MOV? %s" % axis.chan_letter),
            ("Drift compensation Offset  ", "DCO? %s" % axis.chan_letter),
            ("Online                     ", "ONL? %s" % axis.channel),
            ("On target                  ", "ONT? %s" % axis.chan_letter),
            ("On target window           ", "SPA? %s 0x07000900" % axis.chan_letter),
            ("ADC Value of input signal  ", "TAD? %s" % axis.channel),
            ("Input Signal Position value", "TSP? %s" % axis.channel),
            ("Velocity control mode      ", "VCO? %s" % axis.chan_letter),
            ("Velocity                   ", "VEL? %s" % axis.chan_letter),
            ("Osensor                    ", "SPA? %s 0x02000200" % axis.channel),
            ("Ksensor                    ", "SPA? %s 0x02000300" % axis.channel),
            ("Digital filter type        ", "SPA? %s 0x05000000" % axis.channel),
            ("Digital filter Bandwidth   ", "SPA? %s 0x05000001" % axis.channel),
            ("Digital filter order       ", "SPA? %s 0x05000002" % axis.channel),
        ]

        _txt = "     PI_E51X STATUS:\n"

        # Read pre-defined infos (1 line answers)
        for i in _infos:
            _txt = _txt + f"        {i[0]} {self.command(i[1])}\n"

        if self.model.startswith("E517"):
            # Communication parameters.
            _txt = _txt + "    %s  \n%s\n" % (
                "Communication parameters",
                "\n".join(self.command("IFC?", nb_line=6)),
            )

            # Firmware version
            _txt = _txt + "    %s  \n%s\n" % (
                "Firmware version",
                "\n".join(self.command("VER?", nb_line=3)),
            )

        if self.model == "E518":
            # Communication parameters.
            _txt = _txt + "    %s  \n%s\n" % (
                "Communication parameters",
                "\n".join(self.command("IFC?", nb_line=5)),
            )

            # Firmware version
            _txt = _txt + "    %s  \n%s\n" % (
                "Firmware version",
                "\n".join(self.command("VER?", nb_line=5)),
            )

        (error_nb, err_str) = self.get_error()
        _txt += f"      ERR nb={error_nb}  : '{err_str}'\n"

        return _txt
