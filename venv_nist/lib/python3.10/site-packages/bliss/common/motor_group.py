# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations
import gevent
import numpy
import uuid
import collections
from collections.abc import Mapping

# absolute import to avoid circular import
import bliss.controllers.motor as motor
from bliss.common.axis.axis import Axis
from bliss.common.axis.state import AxisState
from bliss.common.axis.group_move import (
    GroupMove,
    _prepare_one_controller_motions,
    _start_one_controller_motions,
    _stop_one_controller_motions,
)
from bliss.common.utils import grouped


def is_motor_group(obj):
    return isinstance(obj, _Group)


def Group(*axes_list):
    axes = dict()
    for axis in axes_list:
        if not isinstance(axis, Axis):
            raise ValueError("invalid axis %r" % axis)
        axes[axis.name] = axis

    # ensure a pseudo axis is not present with one of its corresponding real axes
    def check_axes(*axes_to_check):
        grp_axes = axes.values()
        for axis in axes_to_check:
            if isinstance(axis.controller, motor.CalcController):
                names = [
                    grp_axis.name
                    for grp_axis in grp_axes
                    if grp_axis in axis.controller.reals
                ]
                if names:
                    raise RuntimeError(
                        "Virtual axis '%s` cannot be present in group with any of its corresponding real axes: %r"
                        % (axis.name, names)
                    )
                # also check reals, that can be calc axes themselves too
                check_axes(*axis.controller.reals)

    check_axes(*axes.values())

    # group names will be generated, since _Group objects
    # are anonymous
    group_name = uuid.uuid4().hex
    g = _Group(group_name, axes)
    return g


class _Group:
    def __init__(self, name, axes_dict):
        self.__name = name
        self._group_move = GroupMove(self)
        self._axes = dict(axes_dict)

    def __info__(self):
        info = "MOTOR GROUP:"
        info += "\n    axes: " + ", ".join(self.axes)
        info += "\n    is moving: " + str(self.is_moving)
        info += "\n    state: " + str(self.state)
        return info

    @property
    def name(self) -> str:
        return self.__name

    @property
    def axes(self) -> dict[str, Axis]:
        return self._axes

    @property
    def axes_with_reals(self) -> dict[str, Axis]:
        d = dict(self._axes)
        d.update({axis.name: axis for axis in motor.get_real_axes(*self.axes.values())})
        return d

    @property
    def is_moving(self):
        return any(axis.is_moving for axis in self.axes.values())

    def _get_state(self, hardware=False):
        """Read the state of all axes in group

        If hardware is False (default), return state from cache
        If hardware is True, return state from hardware reading

        Each axis can have multiple states, so the group state is an aggregation
        of all axes states and the descriptions are enhanced to contain the name of the axis
        with this state, like: "MOVING (m1: Axis is MOVING, m2: Axis is MOVING)" for 2 axes
        m1 and m2 in MOVING state.

        State is READY only if all axes are READY ; otherwise ready motors, if any, are not
        reported in the group state.
        """
        grp_state = AxisState("READY")
        grp_state_desc = collections.defaultdict(list)
        state_prop = "hw_state" if hardware else "state"

        for i, (name, state) in enumerate(
            [(axis.name, getattr(axis, state_prop)) for axis in self._axes.values()]
        ):
            for axis_state in state._current_states:
                if axis_state == "READY":
                    continue
                grp_state_desc[axis_state].append(
                    f"{name}: {state._state_desc[axis_state]}"
                )
        for state_name, desc in grp_state_desc.items():
            grp_state._state_desc[state_name] = ", ".join(desc)
            grp_state.set(state_name)
        return grp_state

    @property
    def state(self) -> AxisState:
        if self.is_moving:
            return AxisState("MOVING")
        return self._get_state()

    @property
    def hw_state(self) -> AxisState:
        return self._get_state(hardware=True)

    @property
    def position(self) -> dict[Axis, float]:
        return self._dial_or_position("position")

    @property
    def dial(self) -> dict[Axis, float]:
        return self._dial_or_position("dial")

    @property
    def position_with_reals(self) -> dict[Axis, float]:
        """
        Actual user axis positions
        """
        return self._dial_or_position("position", with_reals=True)

    @property
    def dial_with_reals(self) -> dict[Axis, float]:
        """
        Actual dial axis positions
        """
        return self._dial_or_position("dial", with_reals=True)

    def _dial_or_position(self, attr, with_reals=False) -> dict[Axis, float]:
        """
        Actual dial axis positions
        """
        positions_dict = dict()

        if with_reals:
            axes = self.axes_with_reals
        else:
            axes = self.axes

        for axis in axes.values():
            positions_dict[axis] = getattr(axis, attr)
        return positions_dict

    def _check_ready(self):
        initial_state = self.state
        if not initial_state.READY:
            raise RuntimeError("all motors are not ready")

    def _check_number(self, pos):
        """
        Raise a ValueError exception if <pos> is not a valid position.
        Return None if <pos> is valid.
        """
        try:
            res = numpy.isfinite(pos)
        except TypeError:
            # isfinite can fail, for example if pos is None
            raise ValueError
        else:
            if not res:
                raise ValueError

    def rmove(self, *args, wait: bool = True, polling_time=None):
        self.move(*args, wait=wait, relative=True, polling_time=polling_time)

    def move(self, *args, wait: bool = True, relative: bool = False, polling_time=None):
        axis_pos_dict = {}

        if len(args) == 1:
            # a { axis: pos, } dict can be passed
            d = args[0]
            if isinstance(d, Mapping):
                for axis, target_pos in d.items():
                    try:
                        self._check_number(target_pos)
                    except ValueError:
                        raise ValueError(
                            f"axis {axis.name} cannot be moved to position: {target_pos}"
                        )
                    else:
                        axis_pos_dict[axis] = target_pos
            else:
                raise TypeError(
                    "A { axis: position, } mapping is expected or multiple axis, pos arguments; %r was given"
                    % d
                )
        else:
            for axis, target_pos in grouped(args, 2):
                try:
                    self._check_number(target_pos)
                except ValueError:
                    raise ValueError(
                        f"axis {axis.name} cannot be moved to position: {target_pos}"
                    )
                else:
                    axis_pos_dict[axis] = target_pos

        self._group_move = GroupMove(self)
        self._group_move.move(
            axis_pos_dict,
            _prepare_one_controller_motions,
            _start_one_controller_motions,
            _stop_one_controller_motions,
            relative=relative,
            wait=wait,
            polling_time=polling_time,
        )

    def wait_move(self):
        """Wait until the end of the motion"""
        self._group_move.wait()

    def stop(self, wait=True):
        """Stop the motion on all motors"""
        self._group_move.stop(wait)


class TrajectoryGroup:
    """
    Group for motor trajectory
    """

    def __init__(self, *trajectories, **kwargs):
        calc_axis = kwargs.pop("calc_axis", None)
        self.__trajectories = trajectories
        self.__trajectories_dialunit = None
        self.__group = Group(*self.axes)
        self.__calc_axis = calc_axis
        self.__disabled_axes = set()

    @property
    def trajectories(self):
        """
        Get/Set trajectories for this movement
        """
        return self.__trajectories

    @trajectories.setter
    def trajectories(self, trajectories):
        self.__trajectories = trajectories
        self.__trajectories_dialunit = None
        self.__group = Group(*self.axes)

    @property
    def axes(self):
        """
        Axes for this motion
        """
        return [t.axis for t in self.__trajectories]

    @property
    def disabled_axes(self):
        """
        Axes which are disabled for the next motion
        """
        return self.__disabled_axes

    def disable_axis(self, axis):
        """
        Disable an axis for the next motion
        """
        self.__disabled_axes.add(axis)

    def enable_axis(self, axis):
        """
        Enable an axis for the next motion
        """
        try:
            self.__disabled_axes.remove(axis)
        except KeyError:  # was already enable
            pass  # should we raise?

    @property
    def calc_axis(self):
        """
        calculation axis if any
        """
        return self.__calc_axis

    @property
    def trajectories_by_controller(self):
        controller_trajectories = dict()
        if self.__trajectories_dialunit is not None:
            for traj in self.__trajectories_dialunit:
                if traj.axis in self.__disabled_axes:
                    continue
                tlist = controller_trajectories.setdefault(traj.axis.controller, [])
                tlist.append(traj)
        return controller_trajectories

    @property
    def is_moving(self) -> bool:
        """
        Return true if a motion action is processing.
        """
        return self.__group.is_moving

    @property
    def state(self) -> AxisState:
        return self.__group.state

    def prepare(self):
        """
        prepare/load trajectories in controllers
        """
        if self.__trajectories_dialunit is None:
            trajectories = list()
            for trajectory in self.trajectories:
                trajectories.append(trajectory.convert_to_dial())
            self.__trajectories_dialunit = trajectories

        prepare = [
            gevent.spawn(controller._prepare_trajectory, *trajectories)
            for controller, trajectories in self.trajectories_by_controller.items()
        ]
        try:
            gevent.joinall(prepare, raise_error=True)
        except BaseException:
            gevent.killall(prepare)
            raise

    def _prepare_move_to_trajectory(self, controller, motions):
        try:
            return controller.prepare_all(*motions)
        except NotImplementedError:
            # this is to "clear" the exception
            # (see issue #3294)
            pass
        for motion in motions:
            controller.prepare_move(motion)

    def _move_to_trajectory(self, controller, motions):
        trajectories = self.trajectories_by_controller[controller]
        controller.move_to_trajectory(*trajectories)

    def _stop_trajectory(self, controller, motions):
        trajectories = self.trajectories_by_controller[controller]
        controller.stop_trajectory(*trajectories)

    def _start_trajectory(self, controller, motions):
        trajectories = self.trajectories_by_controller[controller]
        controller.start_trajectory(*trajectories)

    def move_to_start(self, wait=True, polling_time=None):
        """
        Move all enabled motors to the first point of the trajectory
        """
        self.__group._check_ready()

        motions_dict = {}
        for trajectory in self.trajectories:
            pvt = trajectory.pvt
            final_pos = pvt["position"][0]
            motion = trajectory.axis.get_motion(final_pos, polling_time)
            if not motion:
                # already at final pos
                continue
            # no backlash to go to the first position
            # otherwise it may break next trajectory motion (move_to_end)
            motion.backlash = 0
            motions_dict.setdefault(motion.axis.controller, []).append(motion)

        self.__group._group_move.start(
            motions_dict,
            self._prepare_move_to_trajectory,
            self._move_to_trajectory,
            self._stop_trajectory,
            wait=wait,
        )

    def move_to_end(self, wait=True, polling_time=None):
        """
        Move all enabled motors to the last point of the trajectory
        """
        self.__group._check_ready()

        motions_dict = {}
        for trajectory in self.trajectories:
            pvt = trajectory.pvt
            final_pos = pvt["position"][-1]
            motion = trajectory.axis.get_motion(final_pos, polling_time)
            if not motion:
                continue
            motions_dict.setdefault(motion.axis.controller, []).append(motion)

        self.__group._group_move.start(
            motions_dict,
            None,  # no prepare needed
            self._start_trajectory,
            self._stop_trajectory,
            wait=wait,
        )

    def wait_move(self):
        """
        Wait until the end of the motion
        """
        self.__group.wait_move()

    def stop(self, wait=True):
        """
        Stop the motion on all motors
        """
        self.__group.stop(wait)
