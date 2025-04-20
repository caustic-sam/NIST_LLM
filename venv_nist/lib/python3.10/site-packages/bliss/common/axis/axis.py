# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


from __future__ import annotations

import sys
import enum
import gevent
import gevent.event
import gevent.lock
import math
import functools
import numbers
import numpy
import traceback

from bliss import global_map
from bliss.common.cleanup import capture_exceptions
from bliss.common.deprecation import deprecated_warning
from bliss.common.protocols import HasMetadataForDataset, Scannable
from bliss.common.motor_config import MotorConfig
from bliss.common.motor_settings import AxisSettings
from bliss.common import event
from bliss.common.utils import with_custom_members
from bliss.config.channels import Channel
from bliss.common.logtools import log_debug, log_warning, log_error
from bliss.common.utils import rounder
from bliss.common.utils import autocomplete_property
from bliss.comm.exceptions import CommunicationError
from bliss.common.closed_loop import ClosedLoop
from bliss.common.axis.state import AxisState
from bliss.common.axis.error import AxisOnLimitError, AxisOffError, AxisFaultError
from bliss.common.axis.motion import Motion
from bliss.common.axis.group_move import (
    GroupMove,
    _emit_move_done,
    _prepare_one_controller_motions,
    _start_one_controller_motions,
    _stop_one_controller_motions,
)
from prompt_toolkit.formatted_text import FormattedText
from bliss.shell.formatters import tabulate


#: Default polling time
DEFAULT_POLLING_TIME = 0.02


def float_or_inf(value, inf_sign=1):
    if value is None:
        value = float("inf")
        sign = math.copysign(1, inf_sign)
    else:
        sign = 1
    value = float(value)  # accepts float or numpy array of 1 element
    return sign * value


class Modulo:
    def __init__(self, mod=360):
        self.modulo = mod

    def __call__(self, axis):
        dial_pos = axis.dial
        axis._Axis__do_set_dial(dial_pos % self.modulo)


def lazy_init(func):
    """Decorator to call `self._lazy_init()` before the use of a function."""

    @functools.wraps(func)
    def func_wrapper(self, *args, **kwargs):
        self._lazy_init()
        return func(self, *args, **kwargs)

    return func_wrapper


@with_custom_members
class Axis(Scannable, HasMetadataForDataset):
    """
    This class is typically used by motor controllers in bliss to export
    axis with harmonised interface for users and configuration.
    """

    class READ_POSITION_MODE(enum.Enum):
        CONTROLLER = 1
        ENCODER = 2

    def __init__(self, name, controller, config):
        self.__name = name
        self.__controller = controller
        self.__move_done = gevent.event.Event()
        self.__move_done_callback = gevent.event.Event()
        self.__move_done.set()
        self.__move_done_callback.set()
        self.__motion_hooks = []
        for hook in config.get("motion_hooks", []):
            hook._add_axis(self)
            self.__motion_hooks.append(hook)
        self.__encoder = config.get("encoder")
        if self.__encoder is not None:
            self.__encoder.axis = self
        self.__config = MotorConfig(config)
        self.__settings = AxisSettings(self)
        self._init_config_properties()
        self.__no_offset = False
        self._group_move = GroupMove()
        self._lock = gevent.lock.Semaphore()
        self.__positioner = True
        self._disabled: bool = False
        self._disabled_exception: traceback.TracebackException | None = None
        self._closed_loop: ClosedLoop | None
        if config.get("closed_loop"):
            self._closed_loop = ClosedLoop(self)
        else:
            self._closed_loop = None

        self._display_digits = None

        try:
            config.parent
        except AttributeError:
            # some Axis don't have a controller, e.g. SoftAxis
            disabled_cache = list()
        else:
            disabled_cache = config.parent.get(
                "disabled_cache", []
            )  # get it from controller (parent)
        disabled_cache.extend(config.get("disabled_cache", []))  # get it for this axis
        for setting_name in disabled_cache:
            self.settings.disable_cache(setting_name)

        # self.config ?
        self._unit = self.config.get("unit", str, None)
        self._polling_time = config.get("polling_time", DEFAULT_POLLING_TIME)
        global_map.register(self, parents_list=["axes", controller])

        # create Beacon channels
        self.settings.init_channels()
        self._move_stop_channel = Channel(
            f"axis.{self.name}.move_stop",
            default_value=False,
            callback=self._external_stop,
            self_updates=False,
        )
        self._jog_velocity_channel = Channel(
            f"axis.{self.name}.change_jog_velocity",
            default_value=None,
            callback=self._set_jog_velocity,
            self_updates=False,
        )

    def _lazy_init(self):
        """Initialization triggered at any use of this axis.

        Raises an exception if the axis was flagged as `disabled`
        or if it was not possible to initialize the controller.
        """
        if self._disabled:
            raise RuntimeError(f"Axis {self.name} is disabled")
        try:
            self.controller._initialize_axis(self)
            if not self.controller.axis_initialized(self):
                raise RuntimeError("Disabled by the controller")
        except Exception as e:
            if isinstance(e, CommunicationError):
                # also disable the controller
                self.controller._disabled = True
            self._disabled = True

            # Store the trace without frame references
            exc_type, exc_value, tb = sys.exc_info()
            cleared_tb = traceback.TracebackException(
                *sys.exc_info(), capture_locals=False
            )
            self._disabled_exception = cleared_tb
            raise

    def __close__(self):
        self.close()

    def close(self):
        self.controller.close()

    def axis_rounder(self, value: int | float):
        """
        Return a rounded value of <value> as a string.

        Use `display_digits` as number of digits after decimal point.
        Use `bliss.common.utils.rounder` function to perform the rounding.
        """

        if not isinstance(value, numbers.Number):
            return value

        if math.isnan(value):
            return "nan"

        # Convert a number of digits to a model string usable by rounder() function.
        # ex: 2 -> 0.01 ; 8 -> 0.00000001
        try:
            display_model = float(
                f"{1/pow(10,self.display_digits):0.{self.display_digits}f}"
            )
        except ValueError as val_err:
            raise RuntimeError(
                f"axis {self.name}: error on display_digits:{self.display_digits} {type(self.display_digits)}"
            ) from val_err

        # print("display_model=", display_model)
        rounded_pos_str = rounder(display_model, value)
        return rounded_pos_str

    @property
    def _is_calc_axis(self):
        return False

    @property
    def _check_encoder(self):
        return self.config.get("check_encoder", bool, self.encoder) and self.encoder

    @property
    def _read_position_mode(self):
        if self.config.get("read_position", str, "controller") == "encoder":
            return self.READ_POSITION_MODE.ENCODER
        else:
            return self.READ_POSITION_MODE.CONTROLLER

    @property
    def _encoder_read_mode(self):
        return self._read_position_mode == self.READ_POSITION_MODE.ENCODER

    @property
    def no_offset(self):
        return self.__no_offset

    @no_offset.setter
    def no_offset(self, value):
        self.__no_offset = value

    @property
    def unit(self):
        """unit used for the Axis (mm, deg, um...)"""
        return self._unit

    @property
    def name(self):
        """name of the axis"""
        return self.__name

    @property
    def fullname(self) -> str:
        """Retrieve the channel name from this axis"""
        return f"axis:{self.__name}"

    @property
    def _positioner(self):
        """Axis positioner"""
        return self.__positioner

    @_positioner.setter
    def _positioner(self, new_p):
        self.__positioner = new_p

    @autocomplete_property
    def controller(self):
        """
        Motor controller of the axis
        Reference to :class:`~bliss.controllers.motor.Controller`
        """
        return self.__controller

    @property
    def config(self):
        """Reference to the :class:`~bliss.common.motor_config.MotorConfig`"""
        return self.__config

    @property
    def settings(self):
        """
        Reference to the
        :class:`~bliss.controllers.motor_settings.AxisSettings`
        """
        return self.__settings

    @property
    def is_moving(self):
        """
        Tells if the axis is moving (:obj:`bool`)
        """
        return not self.__move_done.is_set()

    def _init_config_properties(
        self, velocity=True, acceleration=True, limits=True, sign=True, backlash=True
    ):
        self.__steps_per_unit = self.config.get("steps_per_unit", float, 1)
        self.__tolerance = self.config.get("tolerance", float, 1e-4)
        if velocity:
            if "velocity" in self.settings.config_settings:
                self.__config_velocity = self.config.get("velocity", float)
            if "jog_velocity" in self.settings.config_settings:
                self.__config_jog_velocity = self.config.get(
                    "jog_velocity", float, self.__config_velocity
                )
            self.__config_velocity_low_limit = self.config.get(
                "velocity_low_limit", float, float("inf")
            )
            self.__config_velocity_high_limit = self.config.get(
                "velocity_high_limit", float, float("inf")
            )
        if acceleration:
            if "acceleration" in self.settings.config_settings:
                self.__config_acceleration = self.config.get("acceleration", float)
        if limits:
            self.__config_low_limit = self.config.get("low_limit", float, float("-inf"))
            self.__config_high_limit = self.config.get(
                "high_limit", float, float("+inf")
            )
        if backlash:
            self.__config_backlash = self.config.get("backlash", float, 0)

    @property
    def steps_per_unit(self):
        """Current steps per unit (:obj:`float`)"""
        return self.__steps_per_unit

    @property
    def config_backlash(self):
        """Current backlash in user units (:obj:`float`)"""
        return self.__config_backlash

    @property
    def backlash(self):
        """Current backlash in user units (:obj:`float`)"""
        backlash = self.settings.get("backlash")
        if backlash is None:
            return 0
        return backlash

    @backlash.setter
    def backlash(self, backlash):
        self.settings.set("backlash", backlash)

    @property
    @lazy_init
    def closed_loop(self):
        """
        Closed loop object associated to axis.
        """
        return self._closed_loop

    @property
    def tolerance(self):
        """Current Axis tolerance in dial units (:obj:`float`)"""
        return self.__tolerance

    @property
    def encoder(self):
        """
        Reference to :class:`~bliss.common.encoder.Encoder` or None if no
        encoder is defined
        """
        return self.__encoder

    @property
    def motion_hooks(self):
        """Registered motion hooks (:obj:`MotionHook`)"""
        return self.__motion_hooks

    @property
    def offset(self):
        """Current offset in user units (:obj:`float`)"""
        offset = self.settings.get("offset")
        if offset is None:
            return 0
        return offset

    @offset.setter
    def offset(self, new_offset):
        if self.no_offset:
            raise RuntimeError(
                f"{self.name}: cannot change offset, axis has 'no offset' flag"
            )
        self.__do_set_position(offset=new_offset)

    @property
    def sign(self):
        """Current motor sign (:obj:`int`) [-1, 1]"""
        sign = self.settings.get("sign")
        if sign is None:
            return 1
        return sign

    @sign.setter
    @lazy_init
    def sign(self, new_sign):
        new_sign = float(
            new_sign
        )  # works both with single float or numpy array of 1 element
        new_sign = math.copysign(1, new_sign)
        if new_sign != self.sign:
            if self.no_offset:
                raise RuntimeError(
                    f"{self.name}: cannot change sign, axis has 'no offset' flag"
                )
            self.settings.set("sign", new_sign)
            # update pos with new sign, offset stays the same
            # user pos is **not preserved** (like spec)
            self.position = self.dial2user(self.dial)

    def set_setting(self, *args):
        """Sets the given settings"""
        self.settings.set(*args)

    def get_setting(self, *args):
        """Return the values for the given settings"""
        return self.settings.get(*args)

    def has_tag(self, tag):
        """
        Tells if the axis has the given tag

        Args:
            tag (str): tag name

        Return:
            bool: True if the axis has the tag or False otherwise
        """
        for t, axis_list in self.__controller._tagged.items():
            if t != tag:
                continue
            if self.name in [axis.name for axis in axis_list]:
                return True
        return False

    @property
    def disabled(self):
        try:
            self._lazy_init()
        except Exception:
            pass
        return self._disabled

    def enable(self):
        self._disabled = False
        self._disabled_exception = None
        self.hw_state  # force update

    @lazy_init
    def on(self):
        """Turns the axis on"""
        if self.is_moving:
            return

        self.__controller.set_on(self)
        state = self.__controller.state(self)
        self.settings.set("state", state)

    @lazy_init
    def off(self):
        """Turns the axis off"""
        if self.is_moving:
            raise RuntimeError("Can't set power off while axis is moving")

        self.__controller.set_off(self)
        state = self.__controller.state(self)
        self.settings.set("state", state)

    @property
    @lazy_init
    def _set_position(self):
        sp = self.settings.get("_set_position")
        if sp is not None:
            return sp
        position = self.position
        self._set_position = position
        return position

    @_set_position.setter
    @lazy_init
    def _set_position(self, new_set_pos):
        new_set_pos = float(
            new_set_pos
        )  # accepts both float or numpy array of 1 element
        self.settings.set("_set_position", new_set_pos)

    @property
    @lazy_init
    def measured_position(self):
        """
        Return measured position (ie: usually the encoder value).

        Returns:
            float: encoder value in user units
        """
        return self.dial2user(self.dial_measured_position)

    @property
    @lazy_init
    def dial_measured_position(self):
        """
        Dial encoder position.

        Returns:
            float: Dial encoder position
        """
        if self.encoder is not None:
            return self.encoder.read()
        else:
            raise RuntimeError("Axis '%s` has no encoder." % self.name)

    def __do_set_dial(self, new_dial):
        user_pos = self.position

        # Set the new dial on the encoder
        if self._encoder_read_mode:
            dial_pos = self.encoder.set(new_dial)
        else:
            # Send the new value in motor units to the controller
            # and read back the (atomically) reported position
            new_hw = new_dial * self.steps_per_unit
            hw_pos = self.__controller.set_position(self, new_hw)
            dial_pos = hw_pos / self.steps_per_unit
        self.settings.set("dial_position", dial_pos)

        if self.no_offset:
            self.__do_set_position(dial_pos, offset=0)
        else:
            # set user pos, will recalculate offset
            # according to new dial
            self.__do_set_position(user_pos)

        return dial_pos

    @property
    @lazy_init
    def dial(self):
        """
        Return current dial position, or set dial

        Returns:
            float: current dial position (dimensionless)
        """
        dial_pos = self.settings.get("dial_position")
        if dial_pos is None:
            dial_pos = self._update_dial()
        return dial_pos

    @dial.setter
    @lazy_init
    def dial(self, new_dial):
        if self.is_moving:
            raise RuntimeError(
                "%s: can't set axis dial position " "while moving" % self.name
            )
        new_dial = float(new_dial)  # accepts both float or numpy array of 1 element
        old_offset = self.axis_rounder(self.offset)
        old_dial = self.dial
        new_dial = self.__do_set_dial(new_dial)
        new_offset = self.axis_rounder(self.offset)
        print(
            f"'{self.name}` dial position reset from {old_dial} to {new_dial} ; "
            f"offset changed from {old_offset} to {new_offset} (sign:{self.sign})"
        )

    def __do_set_position(self, new_pos=None, offset=None):
        dial = self.dial
        curr_offset = self.offset
        if offset is None:
            # calc offset
            offset = new_pos - self.sign * dial
        if math.isnan(offset):
            # this can happen if dial is nan;
            # cannot continue
            return False
        if math.isclose(offset, 0):
            offset = 0
        if not math.isclose(curr_offset, offset):
            self.settings.set("offset", offset)
        if new_pos is None:
            # calc pos from offset
            new_pos = self.sign * dial + offset
        if math.isnan(new_pos):
            # do not allow to assign nan as a user position
            return False
        self.settings.set("position", new_pos)
        self._set_position = new_pos
        return True

    @property
    @lazy_init
    def position(self):
        """
        Return current user position, or set new user position in user units.

        Returns
        -------
            float: current user position (user units)

        Parameters
        ----------
        new_pos : float
            New position to set, in user units.

        Note
        ----
        This update offset.

        """
        pos = self.settings.get("position")
        if pos is None:
            pos = self.dial2user(self.dial)
            self.settings.set("position", pos)
        return pos

    @position.setter
    @lazy_init
    def position(self, new_pos):
        """See property getter"""
        log_debug(self, "axis.py : position(new_pos=%r)" % new_pos)
        if self.is_moving:
            raise RuntimeError(
                "%s: can't set axis user position " "while moving" % self.name
            )
        new_pos = float(new_pos)  # accepts both float or numpy array of 1 element
        old_offset = self.axis_rounder(self.offset)
        curr_pos = self.position
        if self.no_offset:
            self.dial = new_pos
        if self.__do_set_position(new_pos):
            new_offset = self.axis_rounder(self.offset)
            print(
                f"'{self.name}` position reset from {curr_pos} to {new_pos} ; "
                f"offset changed from {old_offset} to {new_offset} (sign:{self.sign})"
            )

    @property
    def display_digits(self):
        """
        Return number of digits to use in position display.

        This value is determined according to the following rules:

        - use `display_digits` if defined in config.
        - use same number of digits than:
            - `axis.steps_per_unit` if steps_per_unit != 1
            - `axis.tolerance` otherwise

        NB: `axis.tolerance` should always exist.
        """
        # Cached value.
        if self._display_digits is not None:
            return self._display_digits

        # Use `display_digits` value in config in priority.
        self._display_digits = self.config.get("display_digits")

        # `display_digits` not found in config => calculate a default value.
        if self._display_digits is None:
            if self.steps_per_unit < 2:  # Include usual case `steps_per_unit`==1.
                # Use tolerance.
                tol = self.tolerance
                #                print(f"{self.name} USE TOL")
                if tol >= 1:
                    self._display_digits = 2
                else:
                    # Count number of leading zeros in decimal part and add 1.
                    # * tolerance =  0.01          -> digits = 2
                    # * tolerance =  0.0001        -> digits = 4
                    # * tolerance = 12             -> digits = 2
                    # * tolerance = 1e-5 = 0.00001 -> digits = 5
                    self._display_digits = 1 + len(
                        str(f"{float(tol):.15f}").rstrip("0").split(".")[1]
                    )
            else:
                # Use steps_per_unit
                # * steps_per_unit = 555 -> 1 step = 0.0018 -> digits = 4
                # * steps_per_unit =   0.1  -> digits = 0
                if self.steps_per_unit <= 1:
                    self._display_digits = 0
                self._display_digits = len(str(int(self.steps_per_unit))) + 1

        # Ensure value is an integer.
        if not isinstance(self._display_digits, int):
            log_error(
                self,
                f"in display_digits calculation for axis {self.name}: {self._display_digits} (use default: 5)",
            )
            self._display_digits = 5

        return self._display_digits

    @lazy_init
    def _update_dial(self, update_user=True):
        dial_pos = self._hw_position
        update_list = (
            "dial_position",
            dial_pos,
        )
        if update_user:
            update_list += (
                "position",
                self.dial2user(dial_pos, self.offset),
            )
        self.settings.set(*update_list)
        return dial_pos

    @property
    @lazy_init
    def _hw_position(self):
        if self._encoder_read_mode:
            return self.dial_measured_position
        try:
            curr_pos = self.__controller.read_position(self) / self.steps_per_unit
        except NotImplementedError:
            # this controller does not have a 'position'
            # (e.g like some piezo controllers)
            curr_pos = 0
        return curr_pos

    @property
    @lazy_init
    def state(self):
        """
        Return the axis state

        Return:
            AxisState: axis state
        """
        if self.is_moving:
            return AxisState("MOVING")
        state = self.settings.get("state")
        if state is None:
            # really read from hw
            state = self.hw_state
            self.settings.set("state", state)
        return state

    @property
    @lazy_init
    def hw_state(self):
        """Return the current hardware axis state (:obj:`AxisState`)"""
        return self.__controller.state(self)

    def __info__(self):
        """Standard method called by BLISS Shell info helper:
        Return common axis information about the axis and controller specific information.
        Return FormattedText to deal with nice colors :)
        """
        try:
            self._lazy_init()
        except Exception:
            pass

        if self.disabled:
            info_string = FormattedText([("", f"AXIS {self.name} is disabled")])
            if self._disabled_exception is not None:
                lines = self._disabled_exception.format()
                info_string += FormattedText([("", "\n\nREASON\n\n")])
                info_string += FormattedText([("", "".join(lines))])
            return info_string

        from bliss.common.standard import info

        info_string = FormattedText([("", f"AXIS {self.name}\n")])

        if self.unit is not None:
            unit = "(" + self.unit + ")"
        else:
            unit = ""

        # position dial offset sign velocity acc spu tolerance
        info_lines = []
        info_lines.append(
            [
                ("class:header", f"position{unit} "),
                ("class:header", f"dial{unit}"),
                ("class:header", f"offset{unit} "),
                ("class:header", "sign"),
                ("class:header", "steps_per_unit"),
                ("class:header", f"tolerance{unit}"),
            ]
        )

        try:
            info_lines.append(
                [
                    ("class:primary", f"{self.axis_rounder(self.position)}"),
                    f"{self.axis_rounder(self.dial)}",
                    f"{self.axis_rounder(self.offset)}",
                    self.sign,
                    f"{self.steps_per_unit:.2f}",
                    f"{self.tolerance}",
                ]
            )
        except Exception:
            info_lines.append(["Unable to get info..."])

        info_string += FormattedText([("", "\n")])
        info_string += tabulate.tabulate(info_lines)
        info_string += FormattedText([("", "\n")])
        info_string += FormattedText([("", "\n")])

        # SETTINGS WITH CONFIG VALUES
        swc_lines = []
        _low_cfg_limit, _high_cfg_limit = self.config_limits

        swc_lines.append(["    ", "CURRENT VALUES", "|", "CONFIG VALUES"])
        swc_lines.append(["    ", "--------------", "|", "-------------"])

        try:
            # limits
            swc_lines.append(
                [
                    f"limits {unit} [low ; high]",
                    f"[ {self.low_limit:.5f} ; {self.high_limit:.5f}]",
                    "|",
                    f"[ {_low_cfg_limit:.5f} ; {_high_cfg_limit:.5f}]",
                ]
            )

            try:
                # velocity and velocity limits
                swc_lines.append(
                    [
                        f"velocity ({self.unit}/s)",
                        f"{self.velocity}",
                        "|",
                        f"{self.config_velocity}",
                    ]
                )
                vel_low, vel_high = self.velocity_limits
                vel_config_low, vel_config_high = self.config_velocity_limits

                swc_lines.append(
                    [
                        "velocity limits [low ; high]",
                        f"[ {vel_low:.5f} ; {vel_high:.5f}]",
                        "|",
                        f"[ {vel_config_low:.5f} ; {vel_config_high:.5f}]",
                    ]
                )
            except NotImplementedError:
                pass

            try:
                # acceleration / acctime
                swc_lines.append(
                    [
                        f"acceleration ({self.unit}/s²)",
                        f"{self.acceleration}",
                        "|",
                        f"{self.config_acceleration}",
                    ]
                )
                swc_lines.append(
                    ["acctime (s)", f"{self.acctime}", "|", f"{self.config_acctime}"]
                )
            except NotImplementedError:
                pass

            # backlash
            swc_lines.append(
                [
                    f"backlash {unit}",
                    f"{self.axis_rounder(self.backlash)}",
                    "|",
                    f"{self.axis_rounder(self.config_backlash)}",
                ]
            )
            swc_lines.append(["    ", "", "", ""])

            info_string += tabulate.tabulate(
                swc_lines,
                # tablefmt="plain",
                # colalign=("left", "right", "center", "left"),
            )
            info_string += FormattedText([("", "\n")])

        except Exception:
            info_string += FormattedText([("", "Error reading parameters...\n")])

        # jog_velocity jog_acctime
        # TODO ???

        try:
            # Axis State(s)
            states_line = []
            state = self.state
            for state_flag in state.current_states_names:
                if state_flag in self.state._STANDARD_STATES:
                    states_line.append(
                        (state._STANDARD_STATES_STYLES[state_flag], state_flag + " ")
                    )

                elif state_flag in ["ALARM", "ALARMDESC"]:
                    states_line.append(("class:danger", state_flag + " "))
                elif state_flag == "SCSTOP":
                    states_line.append(("class:info", state_flag + " "))
                else:
                    states_line.append(("", state_flag + " "))

            if len(state.current_states_names) > 1:
                info_string += FormattedText([("", "STATES: ")])
            elif len(state.current_states_names) == 1:
                info_string += FormattedText([("", "STATE: ")])
            else:
                info_string += FormattedText(
                    [("", "STATE: "), ("class:warning", "NOT_READY")]
                )

            info_string += FormattedText(states_line)
            info_string += FormattedText([("", "\n")])

        except Exception:
            info_string += FormattedText([("", "Error reading state...\n")])

        # SPECIFIC AXIS INFO
        try:
            # usage of get_axis_info() to pass axis as param.
            info_string += FormattedText([("", "\n")])
            info_string += FormattedText(
                [("", self.__controller.get_axis_info(self) + "\n")]
            )
        except NotImplementedError:
            pass
        except Exception:
            info_string += FormattedText(
                [
                    (
                        "",
                        f"ERROR: Unable to get axis info from controller ({self.name}.controller.get_axis_info({self.name})) \n",
                    )
                ]
            )

        # ENCODER
        if self.encoder is not None:
            try:
                # Encoder is initialised here if not already done.
                info_string += FormattedText([("", info(self.encoder) + "\n")])
            except Exception:
                info_string += FormattedText(
                    [("", "ERROR: Unable to get encoder info\n")]
                )
        else:
            info_string += FormattedText([("", "ENCODER not present\n")])

        # CLOSED-LOOP
        if not self._disabled:
            if self.closed_loop is not None:
                try:
                    info_string += FormattedText([("", info(self.closed_loop))])
                except Exception:
                    info_string += FormattedText(
                        [("", "ERROR: Unable to get closed loop info\n")]
                    )
            else:
                info_string += FormattedText([("", "CLOSED-LOOP not present\n")])

        # MOTION HOOK
        if self.motion_hooks:
            info_string += FormattedText([("", "MOTION HOOKS:\n")])
            for hook in self.motion_hooks:
                info_string += FormattedText([("", f"          {hook}\n")])
        else:
            info_string += FormattedText([("", "MOTION HOOKS not present\n")])

        # CONTROLLER
        def bliss_obj_ref(obj):

            if hasattr(self.__controller, "name"):
                return self.__controller.name
            return f"{type(self.__controller)}({repr(self.__controller)})"

        if self.controller is not None:
            info_string += FormattedText(
                [
                    (
                        "",
                        f"CONTROLLER:\n    name: {bliss_obj_ref(self.__controller)}  (type ",
                    ),
                    ("class:em", f"{self.name}.controller"),
                    ("", " for more information)"),
                ]
            )

        return info_string

    def sync_hard(self):
        """Forces an axis synchronization with the hardware"""
        self.settings.set("state", self.hw_state)
        self._update_dial()
        self._set_position = self.position
        if self.closed_loop is not None:
            self.closed_loop.sync_hard()
        event.send(self, "sync_hard")

    def _check_velocity_limits(self, new_velocity):
        min_velocity, max_velocity = self.velocity_limits
        if abs(new_velocity) > abs(max_velocity):
            raise ValueError(
                f"Velocity ({new_velocity}) exceeds max. velocity: {max_velocity}"
            )
        if min_velocity != float("inf") and abs(new_velocity) < abs(min_velocity):
            raise ValueError(
                f"Velocity ({new_velocity}) is below min. velocity: {min_velocity}"
            )

    @property
    @lazy_init
    def velocity(self):
        """
        Return or set the current velocity.

        Parameters:
            float: new_velocity in user unit/second
        Return:
            float: current velocity in user unit/second
        """
        # Read -> Return velocity read from motor axis.
        _user_vel = self.settings.get("velocity")
        if _user_vel is None:
            _user_vel = self.__controller.read_velocity(self) / abs(self.steps_per_unit)

        return _user_vel

    @velocity.setter
    @lazy_init
    def velocity(self, new_velocity):
        # Write -> Converts into motor units to change velocity of axis.
        new_velocity = float(
            new_velocity
        )  # accepts both float or numpy array of 1 element
        self._check_velocity_limits(new_velocity)

        if new_velocity < 0:
            raise RuntimeError(
                "Invalid velocity, the velocity cannot be a negative value"
            )

        try:
            self.__controller.set_velocity(
                self, new_velocity * abs(self.steps_per_unit)
            )
        except Exception as err:
            raise ValueError(
                "Cannot set value {} for {}".format(new_velocity, self.name)
            ) from err

        _user_vel = self.__controller.read_velocity(self) / abs(self.steps_per_unit)

        if not math.isclose(new_velocity, _user_vel, abs_tol=1e-4):
            log_warning(
                self,
                f"Controller velocity ({_user_vel}) is different from set velocity ({new_velocity})",
            )

        curr_vel = self.settings.get("velocity")
        if curr_vel != _user_vel:
            print(f"'{self.name}` velocity changed from {curr_vel} to {_user_vel}")
        self.settings.set("velocity", _user_vel)

        return _user_vel

    @property
    @lazy_init
    def config_velocity(self):
        """
        Return the config velocity.

        Return:
            float: config velocity (user units/second)
        """
        return self.__config_velocity

    @property
    @lazy_init
    def config_velocity_limits(self):
        """
        Return the config velocity limits.

        Return:
            (low_limit, high_limit): config velocity (user units/second)
        """
        return self.__config_velocity_low_limit, self.__config_velocity_high_limit

    @property
    def velocity_limits(self):
        return self.velocity_low_limit, self.velocity_high_limit

    @velocity_limits.setter
    def velocity_limits(self, limits):
        try:
            if len(limits) != 2:
                raise TypeError
        except TypeError:
            raise ValueError("Usage: .velocity_limits = low, high")
        ll = float_or_inf(limits[0], inf_sign=1)
        hl = float_or_inf(limits[1], inf_sign=1)
        self.settings.set("velocity_low_limit", ll)
        self.settings.set("velocity_high_limit", hl)

    @property
    @lazy_init
    def velocity_high_limit(self):
        """
        Return the limit max of velocity
        """
        return float_or_inf(self.settings.get("velocity_high_limit"))

    @velocity_high_limit.setter
    @lazy_init
    def velocity_high_limit(self, value):
        self.settings.set("velocity_high_limit", float_or_inf(value))

    @property
    @lazy_init
    def velocity_low_limit(self):
        """
        Return the limit max of velocity
        """
        return float_or_inf(self.settings.get("velocity_low_limit"))

    @velocity_low_limit.setter
    @lazy_init
    def velocity_low_limit(self, value):
        self.settings.set("velocity_low_limit", float_or_inf(value))

    def _set_jog_motion(self, motion, velocity):
        """Set jog velocity to controller

        Velocity is a signed value ; takes direction into account
        """
        velocity_in_steps = velocity * self.sign * self.steps_per_unit
        direction = -1 if velocity_in_steps < 0 else 1
        motion.jog_velocity = abs(velocity_in_steps)
        motion.direction = direction

        backlash = self.backlash / self.sign * self.steps_per_unit
        if backlash:
            if math.copysign(direction, backlash) != direction:
                motion.backlash = backlash
        else:
            # don't do backlash correction
            motion.backlash = 0

    def _get_jog_motion(self):
        """Return motion object if axis is moving in jog mode

        Return values:
        - motion object, if axis is moving in jog mode
        - False if the jog move has been initiated by another BLISS
        - None if axis is not moving, or if there is no jog motion
        """
        if self.is_moving:
            if self._group_move.is_moving:
                for motions in self._group_move.motions_dict.values():
                    for motion in motions:
                        if motion.axis is self and motion.type == "jog":
                            return motion
            else:
                return False

    def _set_jog_velocity(self, new_velocity):
        """Set jog velocity

        If motor is moving, and we are in a jog move, the jog command is re-issued to
        set the new velocity.
        It is expected an error to be raised in case the controller does not support it.
        If the motor is not moving, only the setting is changed.

        Return values:
        - True if new velocity has been set
        - False if the jog move has been initiated by another BLISS ('external move')
        """
        motion = self._get_jog_motion()

        if motion is not None:
            if new_velocity == 0:
                self.stop()
            else:
                if motion:
                    self._set_jog_motion(motion, new_velocity)
                    self.controller.start_jog(
                        self, motion.jog_velocity, motion.direction
                    )
                    print(motion.user_msg)
                else:
                    # jog move has been started externally
                    return False

        if new_velocity:
            # it is None the first time the channel is initialized,
            # it can be 0 to stop the jog move in this case we don't update the setting
            self.settings.set("jog_velocity", new_velocity)

        return True

    @property
    @lazy_init
    def jog_velocity(self):
        """
        Return the current jog velocity.

        Return:
            float: current jog velocity (user units/second)
        """
        # Read -> Return velocity read from motor axis.
        _user_jog_vel = self.settings.get("jog_velocity")
        if _user_jog_vel is None:
            _user_jog_vel = self.velocity
        return _user_jog_vel

    @jog_velocity.setter
    @lazy_init
    def jog_velocity(self, new_velocity):
        new_velocity = float(
            new_velocity
        )  # accepts both float or numpy array of 1 element
        if not self._set_jog_velocity(new_velocity):
            # move started externally => use channel to inform
            self._jog_velocity_channel.value = new_velocity

    @property
    @lazy_init
    def config_jog_velocity(self) -> float:
        """
        Returns the config jog velocity (user_units/second).
        """
        return self.__config_jog_velocity

    @property
    @lazy_init
    def acceleration(self) -> float:
        """
        Returns the acceleration.
        """
        _acceleration = self.settings.get("acceleration")
        if _acceleration is None:
            _ctrl_acc = self.__controller.read_acceleration(self)
            _acceleration = _ctrl_acc / abs(self.steps_per_unit)

        return _acceleration

    @acceleration.setter
    @lazy_init
    def acceleration(self, new_acc: float):
        """
        Parameters:
            new_acc: new acceleration that has to be provided in user_units/s2.

        Return:
            acceleration: acceleration (user_units/s2)
        """
        if self.is_moving:
            raise RuntimeError(
                "Cannot set acceleration while axis '%s` is moving." % self.name
            )
        new_acc = float(new_acc)  # accepts both float or numpy array of 1 element
        # Converts into motor units to change acceleration of axis.
        self.__controller.set_acceleration(self, new_acc * abs(self.steps_per_unit))
        _ctrl_acc = self.__controller.read_acceleration(self)
        _acceleration = _ctrl_acc / abs(self.steps_per_unit)
        curr_acc = self.settings.get("acceleration")
        if curr_acc != _acceleration:
            print(
                f"'{self.name}` acceleration changed from {curr_acc} to {_acceleration}"
            )
        self.settings.set("acceleration", _acceleration)
        return _acceleration

    @property
    @lazy_init
    def config_acceleration(self):
        """
        Acceleration specified in IN-MEMORY config.

        Note
        ----
        this is not necessarily the current acceleration.
        """
        return self.__config_acceleration

    @property
    @lazy_init
    def acctime(self):
        """
        Return the current acceleration time.

        Return:
            float: current acceleration time (second)
        """
        if self.acceleration == 0:
            _acctime = numpy.nan
        else:
            _acctime = abs(self.velocity / self.acceleration)

        return _acctime

    @acctime.setter
    @lazy_init
    def acctime(self, new_acctime):
        # Convert acctime into acceleration.
        new_acctime = float(
            new_acctime
        )  # accept both float or numpy array of 1 element
        self.acceleration = self.velocity / new_acctime

        if self.acceleration == 0:
            _acctime = numpy.nan
        else:
            _acctime = abs(self.velocity / self.acceleration)

        return _acctime

    @property
    def config_acctime(self):
        """
        Return the config acceleration time.
        """
        return abs(self.config_velocity / self.config_acceleration)

    @property
    @lazy_init
    def jog_acctime(self):
        """
        Return the current acceleration time for jog move.

        Return:
            float: current acceleration time for jog move (second)
        """
        if self.acceleration == 0:
            _acctime = numpy.nan
        else:
            _acctime = abs(self.jog_velocity / self.acceleration)

        return _acctime

    @property
    def config_jog_acctime(self):
        """
        Return the config acceleration time.
        """
        if self.config_acceleration == 0:
            _acctime = numpy.nan
        else:
            _acctime = abs(self.config_jog_velocity / self.config_acceleration)

        return _acctime

    @property
    def dial_limits(self):
        ll = float_or_inf(self.settings.get("low_limit"), inf_sign=-1)
        hl = float_or_inf(self.settings.get("high_limit"), inf_sign=1)
        return ll, hl

    @dial_limits.setter
    @lazy_init
    def dial_limits(self, limits):
        """
        Set low, high limits in dial units
        """
        try:
            if len(limits) != 2:
                raise TypeError
        except TypeError:
            raise ValueError("Usage: .dial_limits = low, high")
        ll = float_or_inf(limits[0], inf_sign=-1)
        hl = float_or_inf(limits[1], inf_sign=1)
        self.settings.set("low_limit", ll)
        self.settings.set("high_limit", hl)

    @property
    def limits(self):
        """
        Return or set the current software limits in USER units.

        Return:
            tuple<float, float>: axis software limits (user units)

        Example:

            $ my_axis.limits = (-10,10)

        """
        return tuple(map(self.dial2user, self.dial_limits))

    @limits.setter
    def limits(self, limits):
        # Set limits (low, high) in user units.
        try:
            if len(limits) != 2:
                raise TypeError
        except TypeError:
            raise ValueError("Usage: .limits = low, high")

        # accepts iterable (incl. numpy array)
        self.low_limit, self.high_limit = (
            float(x) if x is not None else None for x in limits
        )

    @property
    def low_limit(self):
        # Return Low Limit in USER units.
        ll, hl = self.dial_limits
        return self.dial2user(ll)

    @low_limit.setter
    @lazy_init
    def low_limit(self, limit):
        # Sets Low Limit
        # <limit> must be given in USER units
        # Saved in settings in DIAL units
        if limit is not None:
            limit = float(limit)  # accepts numpy array of 1 element, or float
            limit = self.user2dial(limit)
        self.settings.set("low_limit", limit)

    @property
    def high_limit(self):
        # Return High Limit in USER units.
        ll, hl = self.dial_limits
        return self.dial2user(hl)

    @high_limit.setter
    @lazy_init
    def high_limit(self, limit):
        # Sets High Limit (given in USER units)
        # Saved in settings in DIAL units.
        if limit is not None:
            limit = float(limit)  # accepts numpy array of 1 element, or float
            limit = self.user2dial(limit)
        self.settings.set("high_limit", limit)

    @property
    def config_limits(self):
        """
        Return a tuple (low_limit, high_limit) from IN-MEMORY config in
        USER units.
        """
        ll_dial = self.__config_low_limit
        hl_dial = self.__config_high_limit
        return tuple(map(self.dial2user, (ll_dial, hl_dial)))

    def _update_settings(self, state=None):
        """Update position and state in redis

        By defaul, state is read from hardware; otherwise the given state is used
        Position is always read.

        In case of an exception (represented as X) during one of the readings,
        state is set to FAULT:

        state | pos | axis state | axis pos
        ------|-----|-----------------------
          OK  | OK  |   state    |  pos
          X   | OK  |   FAULT    |  pos
          OK  |  X  |   FAULT    |  not updated
          X   |  X  |   FAULT    |  not updated
        """
        state_reading_exc = None

        if state is None:
            try:
                state = self.hw_state
            except BaseException as exc:
                # save exception to re-raise it afterwards
                state_reading_exc = exc
                state = AxisState("FAULT")
        try:
            self._update_dial()
        except BaseException:
            state = AxisState("FAULT")
            raise
        finally:
            self.settings.set("state", state)
            if state_reading_exc:
                raise state_reading_exc

    def dial2user(self, position, offset=None):
        """
        Translates given position from DIAL units to USER units

        Args:
            position (float): position in dial units

        Keyword Args:
            offset (float): alternative offset. None (default) means use current offset

        Return:
            float: position in axis user units
        """
        if position is None:
            # see limits
            return None
        if offset is None:
            offset = self.offset
        return (self.sign * position) + offset

    def user2dial(self, position):
        """
        Translates given position from user units to dial units

        Args:
            position (float): position in user units

        Return:
            float: position in axis dial units
        """
        return (position - self.offset) / self.sign

    def _get_encoder_delta_steps(self, encoder_dial_pos):
        """
        Return the difference between given encoder position and motor controller indexer, in steps
        """
        if self._encoder_read_mode:
            controller_steps = self.__controller.read_position(self)
            enc_steps = encoder_dial_pos * self.steps_per_unit
            return controller_steps - enc_steps
        return 0

    def _get_motion(self, user_target_pos, polling_time=None) -> Motion | None:
        dial_target_pos = self.user2dial(user_target_pos)
        dial = self.dial  # read from encoder if self._encoder_read_mode
        target_pos = dial_target_pos * self.steps_per_unit
        delta = target_pos - dial * self.steps_per_unit

        # return if already in position (no motion)
        if self.controller._is_already_on_position(self, delta):
            return None

        motion = Motion(
            self,
            target_pos,
            delta,
        )

        # evaluate backlash correction
        backlash_str = ""
        if self.backlash:
            backlash = self.backlash / self.sign * self.steps_per_unit
            if abs(delta) > 0 and math.copysign(delta, backlash) != delta:
                # move and backlash are not in the same direction;
                # apply backlash correction, the move will happen
                # in 2 steps
                backlash_str = f" (with {self.backlash} backlash)"  # in units
                motion.backlash = backlash  # in steps

        # check software limits (backlash included, encoder excluded)
        low_limit_msg = "%s: move to `%s'%s would exceed low limit (%s)"
        high_limit_msg = "%s: move to `%s'%s would exceed high limit (%s)"
        user_low_limit, user_high_limit = self.limits
        low_limit = self.user2dial(user_low_limit) * self.steps_per_unit
        high_limit = self.user2dial(user_high_limit) * self.steps_per_unit
        if high_limit < low_limit:
            high_limit, low_limit = low_limit, high_limit
            user_high_limit, user_low_limit = user_low_limit, user_high_limit
            high_limit_msg, low_limit_msg = low_limit_msg, high_limit_msg
        if motion.target_pos < low_limit:
            raise ValueError(
                low_limit_msg
                % (self.name, user_target_pos, backlash_str, user_low_limit)
            )
        if motion.target_pos > high_limit:
            raise ValueError(
                high_limit_msg
                % (self.name, user_target_pos, backlash_str, user_high_limit)
            )

        # evaluate encoder motion correction
        motion.encoder_delta = self._get_encoder_delta_steps(dial)

        if polling_time is not None:
            motion.polling_time = polling_time

        return motion

    @lazy_init
    def get_motion(
        self, user_target_pos, relative=False, polling_time=None
    ) -> Motion | None:
        """Prepare a motion. Internal usage only"""

        # To accept both float or numpy array of 1 element
        user_target_pos = float(user_target_pos)

        log_debug(
            self,
            "get_motion: user_target_pos=%g, relative=%r" % (user_target_pos, relative),
        )

        if relative:
            # start from last set position
            user_initial_pos = self._set_position
            user_target_pos += user_initial_pos

        # obtain motion object
        motion = self._get_motion(user_target_pos, polling_time)
        if motion is None:
            # Already in position, just update set_pos
            self._set_position = user_target_pos
            return None

        # check discrepancy
        check_discrepancy = self.config.get("check_discrepancy", bool, True) and (
            not (self._encoder_read_mode and not self._check_encoder)
        )
        if check_discrepancy:
            dial_initial_pos = self.dial
            hw_pos = self._hw_position
            diff_discrepancy = abs(dial_initial_pos - hw_pos)
            if diff_discrepancy > self.tolerance:
                raise RuntimeError(
                    "%s: discrepancy between dial (%f) and controller position (%f)\n \
                        diff=%g tolerance=%g => aborting movement."
                    % (
                        self.name,
                        dial_initial_pos,
                        hw_pos,
                        diff_discrepancy,
                        self.tolerance,
                    )
                )

        return motion

    def _set_moving_state(self, from_channel=False):
        self.__move_done.clear()
        self.__move_done_callback.clear()
        _emit_move_done(self, value=False, from_channel=from_channel)

        moving_state = AxisState("MOVING")
        if from_channel:
            event.send_safe(self, "state", moving_state)
        else:
            self.settings.set("state", moving_state)

    def _set_move_done(self, from_channel=False):
        with capture_exceptions(raise_index=0) as capture:
            with capture():
                if not from_channel:
                    self._update_settings()

            self.__move_done.set()

            with capture():
                _emit_move_done(self, value=True, from_channel=from_channel)

            self.__move_done_callback.set()

    def _check_ready(self):
        if not self.controller.check_ready_to_move(self, self.state):
            raise RuntimeError("axis %s state is " "%r" % (self.name, str(self.state)))

    @lazy_init
    def move(self, user_target_pos, wait=True, relative=False, polling_time=None):
        """
        Move axis to the given absolute/relative position

        Parameters:
            user_target_pos: float
                Destination (user units)
            wait : bool, optional
                Wait or not for end of motion
            relative : bool
                False if *user_target_pos* is given in absolute position or True if it is given in relative position
            polling_time : float
                Motion loop polling time (seconds)

        Raises:
            RuntimeError

        Returns:
            None

        """
        # accepts both floats and numpy arrays of 1 element
        user_target_pos = float(user_target_pos)

        if not numpy.isfinite(user_target_pos):
            raise ValueError(
                f"axis {self.name} cannot be moved to position: {user_target_pos}"
            )

        log_debug(
            self,
            "user_target_pos=%g  wait=%r relative=%r"
            % (user_target_pos, wait, relative),
        )
        with self._lock:
            if self.is_moving:
                raise RuntimeError("axis %s state is %r" % (self.name, "MOVING"))

            self._group_move = GroupMove()
            self._group_move.move(
                {self: user_target_pos},
                _prepare_one_controller_motions,
                _start_one_controller_motions,
                _stop_one_controller_motions,
                relative=relative,
                wait=False,
                polling_time=polling_time,
            )

        if wait:
            self.wait_move()

    def _handle_move(self, motion, ctrl_state_func="state", limit_error=True):
        state = None
        try:
            state = self._move_loop(motion.polling_time, ctrl_state_func, limit_error)
        finally:
            motion.last_state = state
        return state

    def _do_encoder_reading(self):
        enc_dial = self.encoder.read()
        curr_pos = self._update_dial()
        if abs(curr_pos - enc_dial) > self.encoder.tolerance:
            raise RuntimeError(
                f"'{self.name}' didn't reach final position."
                f"(enc_dial={enc_dial:10.5f}, curr_pos={curr_pos:10.5f} "
                f"diff={enc_dial-curr_pos:10.5f} enc.tol={self.encoder.tolerance:10.5f})"
            )

    @lazy_init
    def jog(self, velocity=None, reset_position=None, polling_time=None):
        """
        Start to move axis at constant velocity

        Args:
            velocity: signed velocity for constant speed motion
        """
        if velocity is not None:
            velocity = float(
                velocity
            )  # accepts both floats or numpy arrays of 1 element

            if self._get_jog_motion() is not None:
                # already in jog move
                self.jog_velocity = velocity
                return
        else:
            velocity = self.jog_velocity

        self._check_velocity_limits(velocity)

        with self._lock:
            if self.is_moving:
                raise RuntimeError("axis %s state is %r" % (self.name, "MOVING"))

            if velocity == 0:
                return

            self.jog_velocity = velocity

            motion = Motion(self, None, None, motion_type="jog")
            motion.polling_time = polling_time
            motion.saved_velocity = self.velocity
            motion.reset_position = reset_position
            self._set_jog_motion(
                motion, velocity
            )  # this will complete motion configuration

            def start_jog(controller, motions):
                controller.start_jog(
                    motions[0].axis, motion.jog_velocity, motion.direction
                )

            def stop_one(controller, motions):
                controller.stop_jog(motions[0].axis)

            self._group_move = GroupMove()
            self._group_move.start(
                {self.controller: [motion]},
                None,  # no prepare
                start_jog,
                stop_one,
                "_jog_move",
                wait=False,
            )

    def _jog_move(self, motion):
        return self._handle_move(motion)

    def _jog_cleanup(self, saved_velocity, reset_position):
        self.velocity = saved_velocity

        if reset_position is None:
            self.settings.clear("_set_position")
        elif reset_position == 0:
            self.__do_set_dial(0)
        elif callable(reset_position):
            reset_position(self)

    def rmove(self, user_delta_pos, wait=True, polling_time=None):
        """
        Move axis to the given relative position.

        Same as :meth:`move` *(relative=True)*

        Args:
            user_delta_pos: motor displacement (user units)
        Keyword Args:
            wait (bool): wait or not for end of motion
            polling_time (float): motion loop polling time (seconds)
        """
        log_debug(self, "user_delta_pos=%g  wait=%r" % (user_delta_pos, wait))
        return self.move(
            user_delta_pos, wait=wait, relative=True, polling_time=polling_time
        )

    def _move_loop(self, polling_time, ctrl_state_func, limit_error=True):
        state_funct = getattr(self.__controller, ctrl_state_func)
        while True:
            state = state_funct(self)
            self._update_settings(state)
            if not state.MOVING:
                if limit_error and (state.LIMPOS or state.LIMNEG):
                    raise AxisOnLimitError(
                        f"{self.name}: {str(state)} at {self.position}"
                    )
                elif state.OFF:
                    raise AxisOffError(f"{self.name}: {str(state)}")
                elif state.FAULT:
                    raise AxisFaultError(f"{self.name}: {str(state)}")
                return state
            gevent.sleep(polling_time)

    @lazy_init
    def stop(self, wait=True):
        """
        Stops the current motion

        If axis is not moving returns immediately

        Args:
            wait (bool): wait for the axis to decelerate before returning \
            [default: True]
        """
        if self.is_moving:
            if self._group_move._move_task:
                self._group_move.stop(wait)
            else:
                # move started externally
                self._move_stop_channel.value = True

            if wait:
                self.wait_move()

    def wait_move(self):
        """
        Wait for the axis to finish motion (blocks current :class:`Greenlet`)
        """
        try:
            self.__move_done_callback.wait()
        except BaseException:
            self.stop(wait=False)
            raise
        finally:
            self._group_move.wait()

    def _external_stop(self, stop):
        if stop:
            self.stop()

    @lazy_init
    def home(self, switch=1, wait=True, polling_time=None):
        """
        Searches the home switch

        Args:
            wait (bool): wait for search to finish [default: True]
        """
        with self._lock:
            if self.is_moving:
                raise RuntimeError("axis %s state is %r" % (self.name, "MOVING"))

            # create motion object for hooks
            motion = Motion(
                self,
                target_pos=switch,
                delta=None,
                motion_type="homing",
                target_name="home",
            )
            motion.polling_time = (
                self._polling_time if polling_time is None else polling_time
            )

            def start_one(controller, motions):
                controller.home_search(motions[0].axis, motions[0].target_pos)

            def stop_one(controller, motions):
                controller.stop(motions[0].axis)

            self._group_move = GroupMove()
            self._group_move.start(
                {self.controller: [motion]},
                None,  # no prepare
                start_one,
                stop_one,
                "_wait_home",
                wait=False,
            )

        if wait:
            self.wait_move()

    def _wait_home(self, motion):
        return self._handle_move(motion, ctrl_state_func="home_state")

    @lazy_init
    def hw_limit(self, limit, wait=True, polling_time=None):
        """
        Go to a hardware limit

        Args:
            limit (int): positive means "positive limit"
            wait (bool): wait for axis to finish motion before returning \
            [default: True]
        """
        limit = int(limit)
        with self._lock:
            if self.is_moving:
                raise RuntimeError("axis %s state is %r" % (self.name, "MOVING"))

            motion = Motion(
                self,
                target_pos=limit,
                delta=None,
                motion_type="limit_search",
                target_name="lim+" if limit > 0 else "lim-",
            )
            motion.polling_time = (
                self._polling_time if polling_time is None else polling_time
            )

            def start_one(controller, motions):
                controller.limit_search(motions[0].axis, motions[0].target_pos)

            def stop_one(controller, motions):
                controller.stop(motions[0].axis)

            self._group_move = GroupMove()
            self._group_move.start(
                {self.controller: [motion]},
                None,  # no prepare
                start_one,
                stop_one,
                "_wait_limit_search",
                wait=False,
            )

        if wait:
            self.wait_move()

    def _wait_limit_search(self, motion):
        return self._handle_move(motion, limit_error=False)

    def settings_to_config(
        self, velocity=True, acceleration=True, limits=True, sign=True, backlash=True
    ):
        """
        Set settings values in in-memory config then save it in file.
        Settings to save can be specified.
        """
        if velocity:
            ll, hl = self.velocity_limits
            self.__config.set("velocity", self.velocity)
            self.__config.set("velocity_low_limit", ll)
            self.__config.set("velocity_high_limit", hl)
        if acceleration:
            self.__config.set("acceleration", self.acceleration)
        if limits:
            ll, hl = self.dial_limits
            self.__config.set("low_limit", ll)
            self.__config.set("high_limit", hl)
        if sign:
            self.__config.set("sign", self.sign)
        if backlash:
            self.__config.set("backlash", self.backlash)

        if any((velocity, acceleration, limits, sign, backlash)):
            self.__config.save()
            self._init_config_properties(
                velocity=velocity,
                acceleration=acceleration,
                limits=limits,
                sign=sign,
                backlash=backlash,
            )

    def apply_config(
        self,
        reload=False,
        velocity=True,
        acceleration=True,
        limits=True,
        sign=True,
        backlash=True,
    ):
        """
        Applies configuration values (yml) to the current settings.

        Note
        ----
        This resets the axis settings to those specified in the config

        Parameters
        ----------
        reload : bool
            if True config files are reloaded by beacon.
        """
        if reload:
            self.config.reload()

        if self._closed_loop is not None:
            self._closed_loop.apply_config(reload)

        if self.encoder is not None:
            self.encoder.apply_config(reload)

        self._init_config_properties(
            velocity=velocity,
            acceleration=acceleration,
            limits=limits,
            sign=sign,
            backlash=backlash,
        )

        if velocity:
            self.settings.clear("velocity")
            self.settings.clear("velocity_low_limit")
            self.settings.clear("velocity_high_limit")
        if acceleration:
            self.settings.clear("acceleration")
        if limits:
            self.settings.clear("low_limit")
            self.settings.clear("high_limit")
        if sign:
            self.settings.clear("sign")
        if backlash:
            self.settings.clear("backlash")

        self._disabled = False
        self._disabled_exception = None

        self.settings.init()

        # update position (needed for sign change)
        pos = self.dial2user(self.dial)
        if self.position != pos:
            try:
                self.position = self.dial2user(self.dial)
            except NotImplementedError:
                pass

    @lazy_init
    def set_event_positions(self, positions):
        dial_positions = self.user2dial(numpy.array(positions, dtype=float))
        step_positions = dial_positions * self.steps_per_unit
        return self.__controller.set_event_positions(self, step_positions)

    @lazy_init
    def get_event_positions(self):
        step_positions = numpy.array(
            self.__controller.get_event_positions(self), dtype=float
        )
        dial_positions = self.dial2user(step_positions)
        return dial_positions / self.steps_per_unit

    @lazy_init
    def dataset_metadata(self):
        return {"name": self.name, "value": self.position}


class ModuloAxis(Axis):
    def __init__(self, *args, **kwargs):
        Axis.__init__(self, *args, **kwargs)

        self._modulo = self.config.get("modulo", float)
        self._in_prepare_move = False

    def __calc_modulo(self, pos):
        return pos % self._modulo

    @property
    def dial(self):
        d = super(ModuloAxis, self).dial
        if self._in_prepare_move:
            return d
        else:
            return self.__calc_modulo(d)

    @dial.setter
    def dial(self, value):
        super(ModuloAxis, self.__class__).dial.fset(self, value)
        return self.dial

    def get_motion(self, user_target_pos, *args, **kwargs):
        user_target_pos = self.__calc_modulo(user_target_pos)
        self._in_prepare_move = True
        try:
            return Axis.get_motion(self, user_target_pos, *args, **kwargs)
        finally:
            self._in_prepare_move = False


class NoSettingsAxis(Axis):
    def __init__(self, *args, **kwags):
        super().__init__(*args, **kwags)
        for setting_name in self.settings.setting_names:
            self.settings.disable_cache(setting_name)


class CalcAxis(Axis):
    @property
    def _is_calc_axis(self):
        return True

    @property
    def state(self):
        return self.controller.state(self)

    @property
    def hw_state(self):
        return self.controller.hw_state(self)

    def sync_hard(self):
        """Forces an axis synchronization with the hardware"""
        for pseudo in self.controller.pseudos:
            if pseudo.is_moving:
                return

        self.controller.sync_hard()

    def update_position(self):
        deprecated_warning(
            kind="method",
            name="update_position",
            replacement="sync_hard",
            reason="for homogeneity reasons",
            since_version="1.11",
            skip_backtrace_count=5,
            only_once=False,
        )
        return self.sync_hard()
