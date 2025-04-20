# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numpy


class Motion:
    """Motion information

    Represents a specific motion. The following members are present:

    * *axis* (:class:`Axis`): the axis to which this motion corresponds to
    * *target_pos* (:obj:`float`): final motion position
    * *delta* (:obj:`float`): motion displacement
    * *backlash* (:obj:`float`): motion backlash

    Note: target_pos and delta can be None, in case of specific motion
    types like homing or limit search
    """

    def __init__(
        self,
        axis,
        target_pos,
        delta,
        motion_type="move",
        target_name=None,
    ):
        self.__axis = axis
        self.__type = motion_type
        self.__target_name = target_name

        self._target_pos_raw = target_pos  # steps
        self._delta_raw = delta  # steps
        self._backlash = 0  # steps
        self._encoder_delta = 0  # steps
        self._polling_time = None  # seconds

        # special jog motion
        self._jog_velocity = None
        self._direction = None

        try:
            self._dial_target_pos = self._target_pos_raw / axis.steps_per_unit
        except TypeError:
            self._dial_target_pos = None

        self._user_target_pos = self.axis.dial2user(
            self._dial_target_pos
        )  # dial2user handles None

    @property
    def axis(self):
        """Return the Axis object associated to this motion"""
        return self.__axis

    @property
    def type(self):
        """Type of motion (move, jog, homing, limit_search, ...)"""
        return self.__type

    @property
    def target_name(self):
        """Descriptive text about the target position for some motion types (None, home, lim+, lim-, ...)"""
        return self.__target_name

    @property
    def backlash(self):
        """Backlash compensation (in steps)"""
        return self._backlash

    @backlash.setter
    def backlash(self, value):
        self._backlash = value

    @property
    def encoder_delta(self):
        """Controller vs Encoder compensation (in steps)"""
        return self._encoder_delta

    @encoder_delta.setter
    def encoder_delta(self, value):
        self._encoder_delta = value

    @property
    def jog_velocity(self):
        """Get jog velocity in steps and unsigned (used by hardware)"""
        return self._jog_velocity

    @jog_velocity.setter
    def jog_velocity(self, value):
        """Set jog velocity in steps and unsigned (used by hardware)"""
        if value < 0:
            raise ValueError(
                f"Motion jog velocity cannot be negative but receive {value}"
            )
        self._jog_velocity = value

    @property
    def direction(self):
        """Get jog direction (in dial/ctrl referential, used by hardware)"""
        return self._direction

    @direction.setter
    def direction(self, value):
        """Set jog direction (in dial/ctrl referential, used by hardware)"""
        if value not in [-1, 1]:
            raise ValueError(f"Motion direction must be in [-1, 1] but receive {value}")
        self._direction = value

    @property
    def polling_time(self):
        """Polling time used during motion monitoring to refresh dial and state values"""
        return (
            self._polling_time
            if self._polling_time is not None
            else self.axis._polling_time
        )

    @polling_time.setter
    def polling_time(self, value):
        self._polling_time = value

    @property
    def dial_target_pos(self):
        """The target position requested by the user expressed as 'dial' value (in motor units).
        Does not include backlash and encoder corrections.
        """
        return self._dial_target_pos

    @property
    def user_target_pos(self):
        """The target position requested by the user expressed as 'user' value (in motor units).
        Does not include backlash and encoder corrections.
        """
        return self._user_target_pos

    @property
    def target_pos_raw(self):
        """The motion target position (in steps) without backlash and encoder corrections.
        It corresponds to the position requested by the user (from cmd) converted into steps.
        """
        return self._target_pos_raw

    @property
    def target_pos(self):
        """The motion target position (in steps) that will be sent to the hardware controller.
        It takes into account the backlash and encoder corrections.
        """
        return self._target_pos_raw - self._backlash + self._encoder_delta

    @property
    def delta_raw(self):
        """Difference between target and current pos: (dial_target - dial, in steps)
        without backlash and encoder corrections.
        """
        return self._delta_raw

    @property
    def delta(self):
        """Difference between target and current pos: (dial_target - dial, in steps).
        Used by controllers working in RELATIVE mode.
        It takes into account the backlash and encoder corrections.
        """
        return self._delta_raw - self._backlash + self._encoder_delta

    @property
    def backlash_motion(self):
        """Return the Motion object corresponding to the final move, if there is backlash"""
        return Motion(self.axis, self.target_pos_raw, delta=self.backlash)

    @property
    def user_msg(self):
        start_ = self.__axis.axis_rounder(self.axis.position)
        if self.type == "jog":
            if self.axis.steps_per_unit != 0:
                velocity = self.jog_velocity / self.axis.steps_per_unit
            else:
                raise ValueError(
                    f"Cannot calculate velocity with steps_per_unit={self.axis.steps_per_unit}"
                )

            direction = self.direction * self.axis.sign  # direction in user
            msg = (
                f"Moving {self.axis.name} from {start_} at velocity {velocity} in "
                f"{'positive' if direction > 0 else 'negative'} direction\n"
                f"Stop motion with: {self.axis.name}.stop()"
            )
            return msg

        else:
            if self.target_name:
                # can be a string in case of special move like limit search, homing...
                end_ = self.target_name
            else:
                if self.user_target_pos is None:
                    return
                end_ = self.__axis.axis_rounder(self.user_target_pos)
            return f"Moving {self.axis.name} from {start_} to {end_}"

    def is_equal(self, other_motion):
        """Compare this motion to another motion and check if they are identical"""
        # Don't overload __eq__ to keep this object hashable!

        if self.axis != other_motion.axis:
            return False

        if self.type != other_motion.type:
            return False

        if self.target_name != other_motion.target_name:
            return False

        if self.delta != other_motion.delta:
            if not numpy.isnan(self.delta) or not numpy.isnan(other_motion.delta):
                return False

        if self.target_pos_raw != other_motion.target_pos_raw:
            if not numpy.isnan(self.target_pos_raw) or not numpy.isnan(
                other_motion.target_pos_raw
            ):
                return False

        return True
