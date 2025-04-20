# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
mockup.py : a mockup controller for bliss.

config :
 'velocity' in unit/s
 'acceleration' in unit/s^2
 'steps_per_unit' in unit^-1  (default 1)
 'backlash' in unit
"""

from __future__ import annotations

import bisect
import math
import time
import random
import gevent
import dataclasses

from bliss.physics.trajectory import LinearTrajectory
from bliss.controllers.motor import Controller
from bliss.common.axis.axis import Axis
from bliss.common.axis.state import AxisState
from bliss.common.axis.motion import Motion as AxisMotion
from bliss.common.closed_loop import ClosedLoopState
from bliss.config.settings import SimpleSetting
from bliss.common.hook import MotionHook
from bliss.common.utils import object_method
from bliss.common.utils import object_attribute_get, object_attribute_set
from bliss.common.logtools import log_debug


def rand_noise(min_deviation, max_deviation):
    """
    Pick a value in:
    ]-max_deviation, -min_deviation] U [min_deviation, max_deviation[
    """
    assert min_deviation <= max_deviation
    abs_val = random.uniform(min_deviation, max_deviation)
    return random.choice((-abs_val, abs_val))


class Motion:
    """Describe a single motion"""

    def __init__(self, pi, pf, velocity, acceleration, hard_limits, ti=None):
        # TODO: take hard limits into account (complicated).
        # For now just shorten the movement
        self.hard_limits = low_limit, high_limit = hard_limits
        if pf > high_limit:
            pf = high_limit
        if pf < low_limit:
            pf = low_limit
        self.trajectory = LinearTrajectory(pi, pf, velocity, acceleration, ti)


class MockupAxis(Axis):
    def __init__(self, *args, **kwargs):
        Axis.__init__(self, *args, **kwargs)

    def get_motion(self, *args, **kwargs):
        motion = Axis.get_motion(self, *args, **kwargs)
        if motion is None:
            self.backlash_move = 0
            self.target_pos = None
        else:
            self.target_pos = motion.target_pos
            self.backlash_move = (
                motion.target_pos / self.steps_per_unit if motion.backlash else 0
            )
        return motion


@dataclasses.dataclass
class _AxisData:
    """Store extra state of a mocked axis"""

    # this is a counter to check if an axis is added multiple times,
    # it is incremented in `initalize_axis()`
    init_count: int

    # those 3 are to simulate a real controller (one with internal settings, that
    # keep those for multiple clients)
    hw_position: SimpleSetting
    curr_acc: SimpleSetting
    curr_velocity: SimpleSetting

    home: set[float]
    hw_limits: tuple[float, float] = float("-inf"), float("+inf")

    motion: Motion | None = None
    t0: float | None = None
    delta: float | None = None
    target: float | None = None

    # Used by a test
    stop_jog_called: bool = False

    # Close loop
    cloop_state: ClosedLoopState = ClosedLoopState.UNKNOWN
    cloop_params: dict = dataclasses.field(default_factory=dict)


class Mockup(Controller):
    """
    Simulated motor controller for tests and demo.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.__encoders = {}
        self.__switches = {}

        self._axes_data: dict[Axis, _AxisData] = {}

        # Custom attributes.
        self.__voltages = {}
        self.__cust_attr_float = {}

        self._hw_state = AxisState("READY")
        self._hw_state.create_state("PARKED", "mot au parking")

    def steps_position_precision(self, axis: Axis):
        """Mockup is really a stepper motor controller"""
        return 1

    def read_hw_position(self, axis: Axis):
        return self._axes_data[axis].hw_position.get()

    def set_hw_position(self, axis: Axis, position: float):
        self._axes_data[axis].hw_position.set(position)

    def raw_write(self, com):
        log_debug(self, f"raw_write: '{com}'")

    def raw_write_read(self, com):
        log_debug(self, f"raw_write_read: '{com}'")
        return "A raw answer..."

    """
    Axes initialization actions.
    """

    def _add_axis(self, axis: Axis):
        self._axes_data[axis] = _AxisData(
            init_count=0,
            hw_position=SimpleSetting(
                f"motor_mockup:{axis.name}:hw_position", default_value=0
            ),
            curr_acc=SimpleSetting(
                f"motor_mockup:{axis.name}:curr_acc", default_value=0
            ),
            curr_velocity=SimpleSetting(
                f"motor_mockup:{axis.name}:curr_velocity", default_value=0
            ),
            motion=None,
            home=set(),
        )

        if self.read_hw_position(axis) is None:
            self.set_hw_position(axis, 0)

    def initialize_hardware_axis(self, axis: Axis):
        if axis.closed_loop is not None:
            axis_data = self._axes_data[axis]
            axis_data.cloop_params["kp"] = axis.closed_loop.kp
            axis_data.cloop_params["ki"] = axis.closed_loop.ki
            axis_data.cloop_params["kd"] = axis.closed_loop.kd

    def initialize_axis(self, axis: Axis):
        log_debug(self, "initializing axis %s", axis.name)

        self.__voltages[axis] = axis.config.get("default_voltage", int, default=220)
        self.__cust_attr_float[axis] = axis.config.get(
            "default_cust_attr", float, default=3.14
        )

        # this is to test axis are initialized only once
        axis_data = self._axes_data[axis]
        axis_data.init_count += 1
        axis_data.stop_jog_called = False

    def initialize_encoder(self, encoder):
        """
        If linked to an axis, encoder initialization is called at axis
        initialization.
        """
        enc_config = self.__encoders.setdefault(encoder, {})
        enc_config.setdefault(
            "measured_noise", {"min_deviation": 0, "max_deviation": 0}
        )
        # mock_offset simulates the coupling between the controller and
        # encoder position. For encoders with no axis, mock_offset simply
        # represents the encoder's absolute position.
        enc_config.setdefault("mock_offset", 0)

    def finalize(self):
        pass

    def _get_axis_motion(self, axis: Axis, t: float | None = None) -> Motion | None:
        """
        Get an updated motion object.

        Also updates the motor hardware position setting if a motion is
        occuring
        """
        motion = self._axes_data[axis].motion

        if motion:
            if t is None:
                t = time.time()
            pos = motion.trajectory.position(t)
            self.set_hw_position(axis, pos)
            if t > motion.trajectory.tf:
                self._axes_data[axis].motion = motion = None
        return motion

    def set_hw_limits(
        self, axis: Axis, low_limit: float | None, high_limit: float | None
    ):
        log_debug(self, "set axis limit low=%s, high=%s", low_limit, high_limit)
        if low_limit is None:
            low_limit = float("-inf")
        if high_limit is None:
            high_limit = float("+inf")
        if high_limit < low_limit:
            raise ValueError("Cannot set hard low limit > high limit")
        ll = axis.user2dial(low_limit) * axis.steps_per_unit
        hl = axis.user2dial(high_limit) * axis.steps_per_unit
        # low limit and high limits may now be exchanged,
        # because of the signs or steps per unit or user<->dial conversion
        if hl < ll:
            ll, hl = hl, ll
        hw_limit = (ll, hl)
        self._axes_data[axis].hw_limits = hw_limit

    def _get_hw_limit(self, axis: Axis) -> tuple[float, float]:
        return self._axes_data[axis].hw_limits

    def start_all(self, *motion_list):
        t0 = time.time()
        for motion in motion_list:
            self.start_one(motion, t0=t0)

    def start_one(self, motion: AxisMotion, t0: float | None = None):
        assert isinstance(motion.target_pos, float)
        axis = motion.axis
        log_debug(self, "moving %s to %s", axis.name, motion.target_pos)
        if self._get_axis_motion(axis):
            raise RuntimeError("Cannot start motion. Motion already in place")
        pos = self.read_position(axis)
        vel = self.read_velocity(axis)
        accel = self.read_acceleration(axis)
        end_pos = motion.target_pos
        if t0 is None:
            t0 = time.time()
        hw_limit = self._get_hw_limit(axis)
        axis_motion = Motion(pos, end_pos, vel, accel, hw_limit, ti=t0)
        self._axes_data[axis].motion = axis_motion

    def start_jog(self, axis: Axis, velocity: float, direction: int):
        axis_data = self._axes_data[axis]
        axis_data.stop_jog_called = False
        t0 = time.time()
        pos = self.read_position(axis)
        self.set_velocity(axis, velocity)
        accel = self.read_acceleration(axis)
        target = float("+inf") if direction > 0 else float("-inf")
        hw_limit = self._get_hw_limit(axis)
        motion = Motion(pos, target, velocity, accel, hw_limit, ti=t0)
        axis_data.motion = motion

    def read_position(self, axis: Axis, t: float | None = None):
        """
        Return the position (measured or desired) taken from controller
        in controller unit (steps).
        """
        gevent.sleep(0.005)  # simulate I/O

        t = t or time.time()
        motion = self._get_axis_motion(axis, t)
        if motion is None:
            pos = self.read_hw_position(axis)
        else:
            pos = motion.trajectory.position(t)
        log_debug(self, "%s position is %s", axis.name, pos)
        if math.isnan(pos):
            # issue 1551: support nan as a position
            return pos
        return int(round(pos))

    def read_encoder(self, encoder):
        """
        Return encoder position.
        unit : 'encoder steps'
        """
        axis = encoder.axis
        if axis:
            motor_pos = self.read_position(axis) / float(axis.steps_per_unit)
            encoder_pos = motor_pos + self.__encoders[encoder]["mock_offset"]
        else:
            encoder_pos = self.__encoders[encoder]["mock_offset"]
        noise = rand_noise(**self.__encoders[encoder]["measured_noise"])
        return (encoder_pos + noise) * encoder.steps_per_unit

    def read_encoder_multiple(self, *encoder_list):
        return [self.read_encoder(enc) for enc in encoder_list]

    def set_encoder(self, encoder, encoder_steps: int):
        target_pos = encoder_steps / encoder.steps_per_unit
        axis = encoder.axis
        if axis:
            motor_pos = self.read_position(axis) / float(axis.steps_per_unit)
            self.__encoders[encoder]["mock_offset"] = target_pos - motor_pos
        else:
            self.__encoders[encoder]["mock_offset"] = target_pos

    """
    VELOCITY
    """

    def read_velocity(self, axis: Axis):
        """
        Return the current velocity taken from controller
        in motor units.
        """
        return self._axes_data[axis].curr_velocity.get() * abs(axis.steps_per_unit)

    def set_velocity(self, axis: Axis, new_velocity: float):
        """
        <new_velocity> is in motor units
        """
        vel = new_velocity / abs(axis.steps_per_unit)
        if vel >= 1e9:
            raise RuntimeError("Invalid velocity")
        self._axes_data[axis].curr_velocity.set(vel)
        return vel

    """
    ACCELERATION
    """

    def read_acceleration(self, axis: Axis):
        """
        must return acceleration in controller units / s2
        """
        return self._axes_data[axis].curr_acc.get() * abs(axis.steps_per_unit)

    def set_acceleration(self, axis: Axis, new_acceleration: float):
        """
        <new_acceleration> is in controller units / s2
        """
        acc = new_acceleration / abs(axis.steps_per_unit)
        if acc >= 1e9:
            raise RuntimeError("Invalid acceleration")
        self._axes_data[axis].curr_acc.set(acc)
        return acc

    """
    ON / OFF
    """

    def set_on(self, axis: Axis):
        self._hw_state.clear()
        self._hw_state.set("READY")

    def set_off(self, axis: Axis):
        self._hw_state.set("OFF")

    """
    Hard limits
    """

    def _check_hw_limits(self, axis: Axis):
        ll, hl = self._get_hw_limit(axis)
        pos = self.read_position(axis)
        if pos <= ll:
            return AxisState("READY", "LIMNEG")
        elif pos >= hl:
            return AxisState("READY", "LIMPOS")
        if self._hw_state.OFF:
            return AxisState("OFF")
        else:
            s = AxisState(self._hw_state)
            s.set("READY")
            return s

    """
    STATE
    """

    def state(self, axis: Axis):
        gevent.sleep(0.005)  # simulate I/O
        motion = self._get_axis_motion(axis)
        if motion is None:
            state = self._check_hw_limits(axis)
            pos = self.read_position(axis)
            home_pos_set = self._axes_data[axis].home
            if any(
                math.isclose(pos, home_pos * axis.steps_per_unit)
                for home_pos in home_pos_set
            ):
                state.set("HOME")
            return state
        else:
            return AxisState("MOVING")

    def stop_jog(self, axis: Axis):
        axis_data = self._axes_data[axis]
        axis_data.stop_jog_called = True
        return Controller.stop_jog(self, axis)

    """
    Must send a command to the controller to abort the motion of given axis.
    """

    def stop(self, axis: Axis, t: float | None = None):
        if t is None:
            t = time.time()
        motion = self._get_axis_motion(axis, t)
        if motion:
            # simulate deceleration
            ti = motion.trajectory.ti
            pi = motion.trajectory.pi
            pf = motion.trajectory.pf
            pa = motion.trajectory.pa
            pb = motion.trajectory.pb
            pos = self.read_position(axis)
            d = 1 if motion.trajectory.positive else -1
            a = motion.trajectory.acceleration
            v = motion.trajectory.velocity

            if math.isinf(pf):
                # jog
                new_pi = pi
                new_pf = pos + d * motion.trajectory.accel_dp
            else:
                if d > 0:
                    # going from pi to pa, pb, then pf
                    if pos < pa:
                        # didn't reach full velocity yet
                        new_pi = pi
                        new_pf = pos + (pos - pi)
                    elif pos > pb:
                        # already decelerrating
                        new_pi = pi
                        new_pf = pf - (pos - pb)
                    else:
                        new_pi = pi
                        new_pf = pf - (pb - pos)
                else:
                    if pos > pa:
                        new_pi = pi
                        new_pf = pos - (pi - pos)
                    elif pos < pb:
                        new_pi = pi
                        new_pf = pf + (pb - pos)
                    else:
                        new_pi = pi
                        new_pf = pf + (pos - pb)

            hw_limit = self._get_hw_limit(axis)
            self._axes_data[axis].motion = Motion(new_pi, new_pf, v, a, hw_limit, ti=ti)

    def stop_all(self, *motion_list):
        t = time.time()
        for motion in motion_list:
            self.stop(motion.axis, t=t)

    """
    HOME and limits search
    """

    def home_search(self, axis: Axis, switch: float):
        vel = self.read_velocity(axis)
        accel = self.read_acceleration(axis)
        home_positions_list = sorted(self._axes_data[axis].home)
        if not home_positions_list:
            raise RuntimeError(f"No home defined for axis {axis.name}")
        # find closest home corresponding to switch direction (positive or negative)
        pos = self.read_position(axis) / axis.steps_per_unit
        if switch >= 0:
            bisect_func = bisect.bisect_right
        else:
            bisect_func = bisect.bisect_left
        i = bisect_func(home_positions_list, pos)
        if i == len(home_positions_list):
            target = home_positions_list[0]
        else:
            if switch >= 0:
                target = home_positions_list[i]
            else:
                target = home_positions_list[i - 1]
        t0 = time.time()
        hw_limit = self._get_hw_limit(axis)
        motion = Motion(
            pos * axis.steps_per_unit,
            target * axis.steps_per_unit,
            vel,
            accel,
            hw_limit,
            ti=t0,
        )
        self._axes_data[axis].motion = motion
        self._axes_data[axis].delta = switch
        self._axes_data[axis].t0 = time.time()

    def home_state(self, axis: Axis):
        return self.state(axis)

    def _set_home_pos(self, axis: Axis, pos: float):
        """Set home position (specific to mockup controller, for tests)"""
        if any(math.isclose(pos, home_pos) for home_pos in self._axes_data[axis].home):
            return
        self._axes_data[axis].home.add(pos)

    def _reset_home_pos(self, axis: Axis):
        self._axes_data[axis].home.clear()

    def limit_search(self, axis: Axis, limit: float):
        target = float("+inf") if limit > 0 else float("-inf")
        pos = self.read_position(axis)
        vel = self.read_velocity(axis)
        accel = self.read_acceleration(axis)
        hw_limit = self._get_hw_limit(axis)
        motion = Motion(pos, target, vel, accel, hw_limit)
        self._axes_data[axis].motion = motion

    def __info__(self):
        """Return information about Controller"""
        info_str = f"Controller name: {self.name}\n"

        return info_str

    def get_axis_info(self, axis: Axis):
        """Return 'mockup'-specific info about <axis>"""
        info_str = "MOCKUP AXIS:\n"
        info_str += f"    this axis ({axis.name}) is a simulation axis\n"

        return info_str

    def get_id(self, axis: Axis):
        return "MOCKUP AXIS %s" % (axis.name)

    def set_position(self, axis: Axis, new_position: float):
        """Set the position of <axis> in controller to <new_position>.
        This method is the way to define an offset for <axis>.
        """
        motion = self._get_axis_motion(axis)
        if motion:
            raise RuntimeError("Cannot set position while moving !")

        self.set_hw_position(axis, new_position)
        self._axes_data[axis].target = new_position

        return new_position

    def put_discrepancy(self, axis: Axis, disc: float):
        """Create a discrepancy (for testing purposes) between axis and
        controller.
        """
        self.set_position(axis, self.read_position(axis) + disc)

    """
    CLOSED LOOP
    """

    def get_closed_loop_requirements(self):
        return ["kp", "ki", "kd"]

    def _do_get_closed_loop_state(self, axis: Axis):
        return self._axes_data[axis].cloop_state

    def activate_closed_loop(self, axis: Axis, onoff: bool = True):
        if onoff:
            self._axes_data[axis].cloop_state = ClosedLoopState.ON
        else:
            self._axes_data[axis].cloop_state = ClosedLoopState.OFF

    def set_closed_loop_param(self, axis: Axis, param: str, value):
        if param not in self.get_closed_loop_requirements():
            raise KeyError(f"Unknown closed loop parameter: {param}")
        axis_data = self._axes_data[axis]
        axis_data.cloop_params[param] = value

    def get_closed_loop_param(self, axis: Axis, param: str):
        if param not in self.get_closed_loop_requirements():
            raise KeyError(f"Unknown closed loop parameter: {param}")
        axis_data = self._axes_data[axis]
        return axis_data.cloop_params[param]

    """
    Custom axis methods
    """

    # VOID VOID
    @object_method
    def custom_park(self, axis: Axis):
        """doc-str of custom_park"""
        log_debug(self, "custom_park : parking")
        self._hw_state.set("PARKED")

    # VOID LONG
    @object_method(types_info=("None", "int"))
    def custom_get_forty_two(self, axis: Axis):
        return 42

    # LONG LONG  + renaming.
    @object_method(name="CustomGetTwice", types_info=("int", "int"))
    def custom_get_twice(self, axis: Axis, LongValue):
        return LongValue * 2

    # STRING STRING
    @object_method(types_info=("str", "str"))
    def custom_get_chapi(self, axis: Axis, value):
        """doc-str of custom_get_chapi"""
        if value == "chapi":
            return "chapo"
        elif value == "titi":
            return "toto"
        else:
            return "bla"

    # STRING VOID
    @object_method(types_info=("str", "None"))
    def custom_send_command(self, axis: Axis, value):
        log_debug(self, "custom_send_command(axis=%s value=%r):" % (axis.name, value))

    # Types by default (None, None)
    @object_method
    def custom_command_no_types(self, axis: Axis):
        print("print with no types")

    @object_method
    def generate_error(self, axis: Axis):
        # For testing purposes.
        raise RuntimeError("Testing Error")

    @object_method(types_info=("float", "None"))
    def custom_set_measured_noise(self, axis: Axis, max_deviation, min_deviation=0):
        """
        Custom axis method to add a random noise, given in user units,
        to measured positions. Use min_deviation to avoid "luckily" clean
        samples in tests which aim to outreach tolerance values.
        By the way we add a ref to the corresponding axis.
        """
        assert min_deviation <= max_deviation
        self.__encoders.setdefault(axis.encoder, {})["measured_noise"] = {
            "min_deviation": min_deviation,
            "max_deviation": max_deviation,
        }

    """
    Custom attributes methods
    """

    @object_attribute_get(type_info="int")
    def get_voltage(self, axis: Axis):
        """doc-str of get_voltage"""
        return self.__voltages.setdefault(axis, 10000)

    @object_attribute_set(type_info="int")
    def set_voltage(self, axis: Axis, voltage):
        """doc-str of set_voltage"""
        self.__voltages[axis] = voltage

    @object_attribute_get(type_info="float")
    def get_cust_attr_float(self, axis: Axis):
        return self.__cust_attr_float.setdefault(axis, 9.999)

    @object_attribute_set(type_info="float")
    def set_cust_attr_float(self, axis: Axis, value):
        self.__cust_attr_float[axis] = value

    def has_trajectory(self):
        return True

    def prepare_trajectory(self, *trajectories):
        pass

    def move_to_trajectory(self, *trajectories):
        pass

    def start_trajectory(self, *trajectories):
        pass

    def stop_trajectory(self, *trajectories):
        pass


class MockupHook(MotionHook):
    """Motion hook used for pytest"""

    class Error(Exception):
        """Mockup hook error"""

        pass

    def __init__(self, name, config):
        super(MockupHook, self).__init__()
        self.name = name
        self.config = config
        self.nb_pre_move = 0
        self.nb_post_move = 0
        self.last_pre_move_args = ()
        self.last_post_move_args = ()

    def pre_move(self, motion_list):
        print(self.name, "in pre_move hook")
        if self.config.get("pre_move_error", False):
            raise self.Error("cannot pre_move")
        self.nb_pre_move += 1
        self.last_pre_move_args = motion_list

    def post_move(self, motion_list):
        print(self.name, "in post_move hook")
        if self.config.get("post_move_error", False):
            raise self.Error("cannot post_move")
        self.nb_post_move += 1
        self.last_post_move_args = motion_list


class FaultyMockup(Mockup):
    def __init__(self, *args, **kwargs):
        Mockup.__init__(self, *args, **kwargs)

        if len(args) == 1:
            config = args[0]
        else:
            # handle old signature: args = [ name, config, axes, encoders, shutters, switches ]
            config = args[1]

        self.bad_state = config.get("bad_state", False)
        self.fault_state = config.get("fault_state", False)
        self.disabled_state = config.get("disabled_state", False)
        self.failure_at_initialization = config.get("failure_at_initialization", False)
        self.bad_start = config.get("bad_start", False)
        self.bad_state_after_start = config.get("bad_state_after_start", False)
        self.bad_stop = config.get("bad_stop", False)
        self.bad_encoder = config.get("bad_encoder", False)
        self.bad_position = config.get("bad_position", False)
        self.bad_position_only_once = config.get("bad_position_only_once", False)
        self.nan_position = config.get("nan_position", False)
        self.position_reading_delay = 0
        self.state_recovery_delay = 1
        self.state_msg_index = 0
        self.__encoders = {}

    def axis_initialized(self, axis):
        res = Mockup.axis_initialized(self, axis)
        if self.failure_at_initialization:
            # If not initialized the axis will be considered
            # as kind of OFFLINE (axis._disabled = True)
            return False
        return res

    def state(self, axis: Axis):
        if self.bad_state:
            self.state_msg_index += 1
            raise RuntimeError("BAD STATE %d" % self.state_msg_index)
        elif self.fault_state:
            self._axes_data[axis].motion = None  # stop motion immediately
            return AxisState("FAULT")
        elif self.disabled_state:
            self._axes_data[axis].motion = None  # stop motion immediately
            return AxisState("DISABLED")
        else:
            return Mockup.state(self, axis)

    def initialize_encoder(self, encoder):
        """
        If linked to an axis, encoder initialization is called at axis
        initialization.
        """
        if self.bad_encoder:
            # Simulate a bug during initialization
            raise ZeroDivisionError()
        else:
            super().initialize_encoder(encoder)

    def _check_hw_limits(self, axis: Axis):
        ll, hl = self._get_hw_limit(axis)
        pos = super().read_position(axis)
        if pos <= ll:
            return AxisState("READY", "LIMNEG")
        elif pos >= hl:
            return AxisState("READY", "LIMPOS")
        if self._hw_state.OFF:
            return AxisState("OFF")
        else:
            s = AxisState(self._hw_state)
            s.set("READY")
            return s

    def start_one(self, motion: AxisMotion, **kw):
        self.state_msg_index = 0
        if self.bad_start:
            raise RuntimeError("BAD START")
        else:
            try:
                return Mockup.start_one(self, motion, **kw)
            finally:
                if self.bad_state_after_start:
                    self.bad_state = True
                    gevent.spawn_later(
                        self.state_recovery_delay, setattr, self, "bad_state", False
                    )

    def stop(self, axis: Axis, t: float | None = None):
        if self.bad_stop:
            raise RuntimeError("BAD STOP")
        else:
            return Mockup.stop(self, axis, t=t)

    def read_position(self, axis: Axis, t=None):
        if self.position_reading_delay > 0:
            gevent.sleep(self.position_reading_delay)
        if self.bad_position:
            raise RuntimeError("BAD POSITION")
        elif self.bad_position_only_once:
            self.bad_position_only_once = False
            raise RuntimeError("BAD POSITION")
        elif self.nan_position:
            return float("nan")
        else:
            return Mockup.read_position(self, axis, t)


class CustomMockup(Mockup):
    def __init__(self, *args, **kwargs):
        Mockup.__init__(self, *args, **kwargs)

        self.axis_settings.add("custom_setting1", str)

    @object_method(types_info=(None, str))
    def set_custom_setting1(self, axis: Axis, new_value=None):
        pass

    def read_custom_setting1(self, axis: Axis):
        pass
