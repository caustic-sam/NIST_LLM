# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import gevent
import gevent.event
import collections
import itertools
import time
import logging

from contextlib import contextmanager

from bliss.common import event
from bliss.common.hook import execute_pre_move_hooks
from bliss.common.cleanup import capture_exceptions
from bliss.common.utils import safe_get
from bliss.common.axis.motion import Motion
from bliss.common.logtools import log_warning

_motion_logger = logging.getLogger("bliss.motion")
_motion_tree_logger = logging.getLogger("bliss.motion_tree")

_MOTION_TASK_INTERRUPTION_TIMEOUT = 2
_MOTION_MAX_KILL = 4
_MOTION_MAX_STOP_ATTEMPT = 4
_MOTION_STOP_ATTEMPT_SLEEP_TIME = 0.02


@contextmanager
def motion_bench(title: str, msg: str = ""):
    now = time.perf_counter()
    _motion_logger.info(f"ENTER: {title} {msg}")
    try:
        yield
    finally:
        elapsed = int((time.perf_counter() - now) * 1000)
        _motion_logger.info(f"EXIT : {title:30s} {elapsed:6d} ms")


def _prepare_one_controller_motions(controller, motions):
    try:
        return controller.prepare_all(*motions)
    except NotImplementedError:
        # this is to "clear" the exception
        # (see issue #3294)
        pass
    for motion in motions:
        controller.prepare_move(motion)


def _start_one_controller_motions(controller, motions):
    try:
        return controller.start_all(*motions)
    except NotImplementedError:
        # this is to "clear" the exception
        # (see issue #3294)
        pass
    for motion in motions:
        controller.start_one(motion)


def _stop_one_controller_motions(controller, motions):
    try:
        return controller.stop_all(*motions)
    except NotImplementedError:
        # this is to "clear" the exception
        # (see issue #3294)
        pass
    for motion in motions:
        controller.stop(motion.axis)


def _emit_move_done(obj, value=True, from_channel=False):
    try:
        if not from_channel:
            # this is used by calculation motors controllers,
            # to distinguish between 'move_done' received via
            # channel update, or when an actual move with the
            # local move loop is done
            event.send_safe(obj, "internal_move_done", value)
    finally:
        # this is the event, any subscriber can register, to
        # know when a move is done
        event.send_safe(obj, "move_done", value)


class GroupMove:
    def __init__(self, parent=None):
        self.parent = parent
        self._motions_dict = {}

        self._move_task = None
        self._moni_tasks = []

        self._interrupted_move = False
        self._initialization_has_failed = False
        self._kill_nbr = 0
        self._kill_max = _MOTION_MAX_KILL
        self._stop_attempt_max = _MOTION_MAX_STOP_ATTEMPT
        self._stop_attempt_sleep_time = _MOTION_STOP_ATTEMPT_SLEEP_TIME
        self._task_interruption_timeout = _MOTION_TASK_INTERRUPTION_TIMEOUT

        self._prepare_motion_func = None
        self._start_motion_func = None
        self._stop_motion_func = None
        self._move_func = None

        self._initialization_event = gevent.event.Event()
        self._move_prepared_event = gevent.event.Event()
        self._move_started_event = gevent.event.Event()
        self._backlash_started_event = gevent.event.Event()
        self._end_of_move_event = gevent.event.Event()
        self._end_of_move_event.set()

    @property
    def task_interruption_timeout(self):
        return self._task_interruption_timeout

    @property
    def motions_dict(self):
        return self._motions_dict

    @motions_dict.setter
    def motions_dict(self, value):
        self._motions_dict = value

    @property
    def motions_iter(self):
        return itertools.chain.from_iterable(self._motions_dict.values())

    @property
    def all_axes(self):
        return (m.axis for m in self.motions_iter)

    @property
    def is_moving(self):
        return any(motion.axis.is_moving for motion in self.motions_iter)

    def _fill_motions_dict(
        self,
        remaining_axes,
        found_axes,
        not_moving_axes,
        axes_motions,
        axis_pos_dict,
        relative,
        polling_time,
        level=0,
    ):

        _motion_tree_logger.info(
            f"=== ENTER level {level} with inputs: {[x.name for x in axis_pos_dict]} ==="
        )

        for axis, target_pos in axis_pos_dict.items():

            if axis in not_moving_axes:
                _motion_tree_logger.info(f" axis {axis.name} is already discarded")
                continue

            motion = axis.get_motion(
                target_pos, relative=relative, polling_time=polling_time
            )
            if motion is None:  # motion can be None if axis is not supposed to move
                not_moving_axes.add(axis)
                _motion_tree_logger.info(f" axis {axis.name} is already in place")
                continue

            elif axes_motions.get(axis):  # a motion for that axis already exist
                cur_motion = axes_motions.get(axis)
                _motion_tree_logger.info(f" motion for axis {axis.name} already exist")
                if not motion.is_equal(cur_motion):
                    msg = f"Found different motions for same axis {motion.axis.name}:\n"
                    msg += f" existing motion: user_target_pos={cur_motion.user_target_pos} delta={cur_motion.delta} type={cur_motion.type} target_name={cur_motion.target_name}\n"
                    msg += f" new motion:      user_target_pos={motion.user_target_pos} delta={motion.delta} type={motion.type} target_name={motion.target_name}\n"
                    raise RuntimeError(msg)

            else:
                axes_motions[axis] = motion
                _motion_tree_logger.info(f" found motion for axis {axis.name}")

            if axis._is_calc_axis:
                _motion_tree_logger.info(f" axis {axis.name} is CalcAxis")
                step1_ok = True
                param_motions = []
                moving_axes = found_axes - not_moving_axes
                for param in axis.controller.params:
                    if (
                        param in moving_axes
                    ):  # this controller has a parametric axis involved in the group motion
                        _motion_tree_logger.info(
                            f" axis {axis.name} has a parametric axis {param.name} with motion"
                        )
                        if not axes_motions.get(
                            param
                        ):  # if param motion is not discovered yet, post pone.
                            step1_ok = False
                            _motion_tree_logger.info(
                                f" adding axis {axis.name} to pending list because {param.name} motion is not known yet"
                            )
                            remaining_axes[axis] = motion.user_target_pos
                            break
                        else:
                            param_motions.append(axes_motions.get(param))

                if step1_ok:
                    # now, we are sure that we have all the motions of the param axes that are moved elsewhere
                    # so we can compute the motion of the reals
                    step2_ok = True
                    pseudo_motions = []
                    for pseudo in axis.controller.pseudos:
                        if pseudo in moving_axes:
                            _motion_tree_logger.info(
                                f" found involved pseudo {pseudo.name}"
                            )
                            if not axes_motions.get(pseudo):
                                step2_ok = False
                                _motion_tree_logger.info(
                                    f" adding axis {axis.name} to pending list because {pseudo.name} motion is not known yet"
                                )
                                break
                            else:
                                pseudo_motions.append(axes_motions.get(pseudo))

                    if step2_ok:
                        # now we are sure that we have all the motions of the pseudo of that controller involved in the motion
                        for pseudo in axis.controller.pseudos:
                            if remaining_axes.pop(pseudo, None):
                                _motion_tree_logger.info(
                                    f" axis {pseudo.name} has been cleared from pending list"
                                )
                        motions = pseudo_motions + param_motions
                        _motion_tree_logger.info(
                            f" computing motions of {[x.axis.name for x in motions]} dependencies"
                        )
                        real_move_dict = axis.controller._get_real_axes_move_dict(
                            motions
                        )
                        self._fill_motions_dict(
                            remaining_axes,
                            found_axes,
                            not_moving_axes,
                            axes_motions,
                            real_move_dict,
                            relative=False,
                            polling_time=polling_time,
                            level=level + 1,
                        )

                        _motion_tree_logger.info(f"=== BACKTO level {level} ===")

                        # discard all pseudo axes of this Calc if all its dependencies are not moving
                        dependencies = set(real_move_dict.keys())
                        if not (dependencies - not_moving_axes):
                            psnames = []
                            for pseudo in axis.controller.pseudos:
                                not_moving_axes.add(pseudo)
                                axes_motions.pop(pseudo, None)
                                psnames.append(pseudo.name)
                            _motion_tree_logger.info(
                                f" discarding {psnames} because {[x.name for x in dependencies]} already in place"
                            )

        _motion_tree_logger.info(f"=== EXIT level {level} ===")
        _motion_tree_logger.info(f" found     : {[x.name for x in axes_motions]}")
        _motion_tree_logger.info(f" pending   : {[x.name for x in remaining_axes]}")
        _motion_tree_logger.info(f" discarded : {[x.name for x in not_moving_axes]}")

    def _find_sub_axes(self, axis_pos_dict, found_axes):
        """
        axis_pos_dict: dict of {axes: target_pos} passed to the motion cmd (absolute positions only!)
        found_axes: list of all axes involved in the motion (including all dependencies)
        """
        # find all axes dependencies (keeps discovery order)
        axes_list = axis_pos_dict.keys()
        found_axes.extend(axes_list)

        # do not rely on CalcController.reals (see issue 4306)
        motions_dict = {}
        for axis in axes_list:
            if axis._is_calc_axis:
                motions_dict.setdefault(axis.controller, []).append(
                    Motion(axis, axis_pos_dict[axis], None)
                )

        for ctrl, motions in motions_dict.items():
            dependencies_pos_dict = ctrl._get_real_axes_move_dict(motions)
            self._find_sub_axes(dependencies_pos_dict, found_axes)

    def _get_motions_per_controller(self, axes_motions):
        motions_dict = {}
        for axis, motion in axes_motions.items():
            motions_dict.setdefault(axis.controller, []).append(motion)
        return motions_dict

    def _find_motion_to_delete(self, motions_dict, excluded_axes):
        from bliss.controllers.motor import CalcController

        # if all motions only concern CalcControllers, cancel the entire motion (see issue 4198)
        if all(
            isinstance(controller, CalcController) for controller in motions_dict.keys()
        ):
            motions_dict.clear()
            _motion_logger.info("All physical axes already in place, motion cancelled")
            return

        for controller, motions in list(motions_dict.items()):
            if isinstance(controller, CalcController):
                if not set(controller.reals) - excluded_axes:
                    excluded_axes |= {motion.axis for motion in motions}
                    _motion_logger.info(
                        f"All Reals of {controller.name} already in place, discarding motions of {[m.axis.name for m in motions]}"
                    )
                    del motions_dict[controller]
                    return True

    def _resolve_motions_tree(self, axis_pos_dict, relative, polling_time):
        """
        'axis_pos_dict' is the dict {axis: target_position} passed to the move cmd.

        This function discovers all axes involved by the move cmd,
        from top level CalcAxis down to physical motors.

        It obtains the motion object for each axis.

        Axes already in place are filtered but it ensures that
        the position of linked CalcAxis is updated.

        Raise an error if different motions are found for same axis.

        Ensures that moving parametric axes are properly handled.

        Returns a dict of motions ordered by controllers
        """

        found_axes = []
        self._find_sub_axes(
            {
                axis: pos if not relative else pos + axis._set_position
                for axis, pos in axis_pos_dict.items()
            },
            found_axes,
        )
        found_axes = list(dict.fromkeys(found_axes))

        # === DEBUG ==========================
        _motion_tree_logger.info(
            f"Axes involved in motion: {[x.name for x in found_axes]}"
        )
        # ====================================

        not_moving_axes = set()
        remaining_axes = {}
        axes_motions = {}
        found_axes = set(found_axes)

        self._fill_motions_dict(
            remaining_axes,
            found_axes,
            not_moving_axes,
            axes_motions,
            axis_pos_dict,
            relative,
            polling_time,
        )

        # deal with axes added to pending list
        while remaining_axes:
            axis_pos_dict = remaining_axes.copy()
            self._fill_motions_dict(
                remaining_axes,
                found_axes,
                not_moving_axes,
                axes_motions,
                axis_pos_dict,
                relative=False,
                polling_time=polling_time,
                level=0,
            )

            if set(axis_pos_dict) == set(remaining_axes):
                raise RuntimeError(
                    f"Unable to resolve motion tree for axes {[x.name for x in remaining_axes]}"
                )

        motions_dict = self._get_motions_per_controller(axes_motions)
        while self._find_motion_to_delete(motions_dict, not_moving_axes):
            pass

        _motion_logger.info(
            f"Axes already in place: {[x.name for x in not_moving_axes]}"
        )
        for axis in not_moving_axes:
            # Different combinations of {pseudo pos, calc params}
            # can lead to the same position of the reals. So reals won't move
            # and won't update pseudos positions via Louie callbacks.
            # So send "internal_position" signal on reals to force linked pseudos to update their positions.
            if not axis._is_calc_axis:
                event.send(axis, "internal_position", axis.position)

        for motions in motions_dict.values():
            for motion in motions:
                _motion_logger.info(motion.user_msg)

        return motions_dict

    def move(
        self,
        axis_pos_dict,
        prepare_motion,
        start_motion,
        stop_motion,
        move_func=None,
        relative=False,
        wait=True,
        polling_time=None,
    ):

        if self._move_task:
            raise RuntimeError(
                "Cannot start a new motion while current motion is still running"
            )

        with motion_bench("resolve_motions_tree"):
            motions_dict = self._resolve_motions_tree(
                axis_pos_dict, relative, polling_time
            )

        if motions_dict:
            self.start(
                motions_dict,
                prepare_motion,
                start_motion,
                stop_motion,
                move_func=move_func,
                wait=wait,
            )

    def start(
        self,
        motions_dict,
        prepare_motion,
        start_motion,
        stop_motion,
        move_func=None,
        wait=True,
    ):

        self._init_vars_on_start(
            motions_dict, prepare_motion, start_motion, stop_motion, move_func
        )

        with motion_bench(f"start motion {'(wait=True)' if wait else ''}"):

            # assign group_move
            for motion in self.motions_iter:
                motion.axis._group_move = self

            # pre move hooks and check ready
            # (automatically perform post move hook if error/not-ready)
            self._do_pre_move_hooks()

            try:
                self._move_task = gevent.spawn(self._perform_move)
                self._move_task.name = "motion_task"

                # ensure motion is initialized before returning
                with motion_bench("wait motion initialization"):
                    self._initialization_event.wait()

                if wait:
                    self.wait()

            except BaseException as e:
                self.stop()
                raise e

    def wait(self):
        if self._move_task is not None:
            with motion_bench("wait motion task"):
                self._move_task.get()

    def stop(self, wait=True):
        if self._move_task:
            _motion_logger.info(f"ABORT: stop motion {'(wait=True)' if wait else ''}")
            if not self._stopping:
                _motion_logger.info("ABORT: killing move task")
                self._stopping = True
                self._move_task.kill(block=False)
            if wait:
                try:
                    self.wait()
                except (KeyboardInterrupt, gevent.GreenletExit):
                    self._kill_nbr += 1
                    if self._kill_nbr > self._kill_max:
                        _motion_logger.info(
                            "ABORT: exit stopping procedure after max KeyboardInterrupt"
                        )
                        raise

                    elif self._kill_nbr == self._kill_max:
                        log_warning(
                            self,
                            f"!!! NEXT CTRL-C WILL INTERRUPT THE STOPPPING PROCEDURE AND CAN LEAVE AXES IN BAD STATE OR STILL RUNNING !!! (stopping attempt: {self._kill_nbr}/{self._kill_max})",
                        )
                        self.stop()

                    else:
                        log_warning(
                            self,
                            f"Motion is stopping, please wait (stopping attempt: {self._kill_nbr}/{self._kill_max})",
                        )
                        self.stop()

    def _request_motion_stop(self):
        """Send controllers stop cmds and retry in case of cmd failure.
        After too many unsuccessful attempts an error is raised.
        """

        with motion_bench("request_motion_stop"):

            retries = 0
            failing_stop_tasks = self._send_stop_cmds(self.motions_dict)

            # retry controller's stop cmds which have failed (up to _stop_attempt_max)
            while failing_stop_tasks:
                retries += 1
                if retries > self._stop_attempt_max:
                    msg_lines = [""]
                    for task, ctrl in failing_stop_tasks.items():
                        axes = [m.axis.name for m in self.motions_dict[ctrl]]
                        msg_lines.append(
                            f"axis {axes} stopping cmd failed with exception: {task.exception}"
                        )
                    msg_lines.append("")
                    raise RuntimeError("\n".join(msg_lines))

                gevent.sleep(self._stop_attempt_sleep_time)
                motions_dict = {
                    ctrl: self.motions_dict[ctrl]
                    for ctrl in failing_stop_tasks.values()
                }
                failing_stop_tasks = self._send_stop_cmds(motions_dict)

    def _init_vars_on_start(
        self, motions_dict, prepare_motion, start_motion, stop_motion, move_func
    ):
        self.motions_dict = motions_dict
        self._prepare_motion_func = prepare_motion
        self._start_motion_func = start_motion
        self._stop_motion_func = stop_motion
        self._move_func = move_func

        self._interrupted_move = False
        self._stopping = False
        self._kill_nbr = 0

        self._initialization_has_failed = False
        self._initialization_event.clear()
        self._move_prepared_event.clear()
        self._move_started_event.clear()
        self._backlash_started_event.clear()

    def _initialize_motion(self):
        with motion_bench("initialize_motion"):
            try:
                # set target position and moving state
                restore_axes = {}
                for motion in self.motions_iter:
                    target_pos = motion.user_target_pos
                    if target_pos is not None:
                        restore_axes[motion.axis] = (
                            motion.axis._set_position,
                            motion.axis.state,
                        )
                        motion.axis._set_position = target_pos

                    msg = motion.user_msg
                    if msg:
                        event.send_safe(motion.axis, "msg", msg)
                        if motion.type != "move":
                            print(msg)

                    motion.axis._set_moving_state()

                if self.parent:
                    _emit_move_done(self.parent, value=False)

            except BaseException:
                self._initialization_has_failed = True
                with motion_bench("restore axes"):
                    for ax, (setpos, state) in restore_axes.items():
                        # revert actions of _set_moving_state
                        ax.settings.set("state", state)
                        ax._set_move_done()
                        ax._set_position = setpos
                raise

            finally:
                self._initialization_event.set()

    def _perform_move(self):

        self._end_of_move_event.clear()

        try:
            self._initialize_motion()

            with motion_bench("perform main motion"):
                self._main_motion_task()

            self._backlash_motion_task()

        except BaseException as e:
            _motion_logger.info(f"ABORT: exception during motion: {e}")
            self._interrupted_move = True

            if self._move_started_event.is_set():

                with capture_exceptions(raise_index=0) as capture:
                    with capture():
                        self._request_motion_stop()
                    if capture.failed:
                        log_warning(
                            self,
                            "ABORT: stop cmd failed, now waiting initial motion to finish",
                        )

                    with motion_bench("joining current monitoring tasks"):
                        if all(
                            [task.dead for task in self._moni_tasks]
                        ):  # all dead or _moni_tasks empty
                            # in case of failure before monitor_motion has been called (but motions started):
                            # case 1: during     main-motion => self._moni_tasks = []
                            # case 2: during backlash-motion => self._moni_tasks = [dead_task, ...]
                            self._monitor_motion(
                                raise_error=False
                            )  # do not raise to join all tasks
                        else:
                            gevent.joinall(self._moni_tasks)

            raise e

        finally:

            try:
                if not self._initialization_has_failed:
                    self._finalize_motion()

                self._do_post_move_hooks()

            finally:
                self._stopping = False
                self._end_of_move_event.set()

    def _main_motion_task(self):
        self._send_prepare_cmds()
        self._send_start_cmds()
        self._monitor_motion()

    def _backlash_motion_task(self):
        backlash_motions = collections.defaultdict(list)
        for controller, motions in self.motions_dict.items():
            for motion in motions:
                if motion.backlash:
                    backlash_motions[controller].append(motion.backlash_motion)

        if backlash_motions:
            bgm = GroupMove()
            bgm._init_vars_on_start(
                backlash_motions,
                _prepare_one_controller_motions,
                _start_one_controller_motions,
                _stop_one_controller_motions,
                None,
            )
            self._backlash_started_event.set()
            with motion_bench("perform backlash motion"):
                bgm._main_motion_task()

    def _do_pre_move_hooks(self):
        with motion_bench("pre move hooks and check ready"):
            with execute_pre_move_hooks(list(self.motions_iter)):
                for motion in self.motions_iter:
                    motion.axis._check_ready()

    def _do_post_move_hooks(self):
        with motion_bench("post_move_hooks"):
            hooks = collections.defaultdict(list)
            for motion in self.motions_iter:
                for hook in motion.axis.motion_hooks:
                    hooks[hook].append(motion)

            with capture_exceptions(raise_index=0) as capture:
                for hook, motions in reversed(list(hooks.items())):
                    with capture():
                        hook.post_move(motions)

    def _send_prepare_cmds(self):
        """Send in parallel controllers prepare cmds.
        If one fails, wait for others to finish before 'self.task_interruption_timeout'
        else kill associated tasks to avoid hanging on blocking prepare cmds.
        """

        with motion_bench("send_prepare_cmds"):
            tasks = []
            if self._prepare_motion_func is not None:
                for controller, motions in self.motions_dict.items():
                    task = gevent.spawn(self._prepare_motion_func, controller, motions)
                    task.name = f"motion_prepare_{controller.name}"
                    tasks.append(task)

            try:
                gevent.joinall(tasks, raise_error=True)
            except BaseException:
                try:
                    with gevent.Timeout(self.task_interruption_timeout):
                        gevent.joinall(tasks)
                except gevent.Timeout:
                    _motion_logger.info(
                        "ABORT: unexpected timeout while joining prepare cmd tasks, now killing tasks"
                    )
                    gevent.killall(tasks)
                raise

            self._move_prepared_event.set()

    def _send_start_cmds(self):
        """Send in parallel controllers start cmds.
        If one fails, wait for others to finish before 'self.task_interruption_timeout'
        else kill associated tasks to avoid hanging on blocking start cmds.
        """

        with motion_bench("send_start_cmds"):
            tasks = []
            for controller, motions in self.motions_dict.items():
                task = gevent.spawn(self._start_motion_func, controller, motions)
                task.name = f"motion_start_{controller.name}"
                tasks.append(task)

            self._move_started_event.set()

            try:
                gevent.joinall(tasks, raise_error=True)
            except BaseException:
                try:
                    with gevent.Timeout(self.task_interruption_timeout):
                        gevent.joinall(tasks)
                except gevent.Timeout:
                    _motion_logger.info(
                        "ABORT: unexpected timeout while joining start cmd tasks, now killing tasks"
                    )
                    gevent.killall(tasks)
                raise

    def _send_stop_cmds(self, motions_dict):
        """Send in parallel controllers stop cmds and return the ones which have failed"""

        axes_to_stop = [
            m.axis.name for m in itertools.chain.from_iterable(motions_dict.values())
        ]
        with motion_bench("send_stop_cmds", f"{axes_to_stop}"):
            tasks = {}
            for controller, motions in motions_dict.items():
                task = gevent.spawn(self._stop_motion_func, controller, motions)
                task.name = f"motion_stop_{controller.name}"
                tasks[task] = controller

            failing_stop_tasks = {}
            for task in gevent.iwait(tasks):
                if task.exception is not None:
                    ctrl = tasks[task]
                    failing_stop_tasks[task] = ctrl

        return failing_stop_tasks

    def _monitor_motion(self, raise_error=True):
        with motion_bench("monitor_motion"):

            if self._move_func is None:
                move_func = "_handle_move"
            else:
                move_func = self._move_func

            self._moni_tasks = []
            for motion in self.motions_iter:
                if motion.axis._is_calc_axis:
                    # calc axes will get updated via real motors updates
                    continue

                task = gevent.spawn(getattr(motion.axis, move_func), motion)
                task.name = f"motion_monitor_{motion.axis.name}"
                self._moni_tasks.append(task)

            gevent.joinall(self._moni_tasks, raise_error=raise_error)

    def _parallel_set_move_done(self):
        with motion_bench("parallel_set_move_done"):
            tasks = []
            for ax in (ax for ax in self.all_axes if not ax._is_calc_axis):
                task = gevent.spawn(ax._set_move_done)
                task.name = f"motion_set_move_done_{ax.name}"
                tasks.append(task)

            try:
                gevent.joinall(tasks, raise_error=True)
            except BaseException:
                gevent.joinall(tasks)
                raise

    def _jog_cleanup(self, motion):
        with motion_bench("perform jog cleanup"):
            motion.axis._jog_cleanup(motion.saved_velocity, motion.reset_position)

    def _finalize_motion(self):
        with motion_bench("finalize_motion"):

            with capture_exceptions(raise_index=0) as capture:

                reset_setpos = self._interrupted_move

                all_motions = list(self.motions_iter)
                if len(all_motions) == 1:
                    motion = all_motions[0]
                    if motion.type == "jog":
                        reset_setpos = False

                        with capture():
                            self._jog_cleanup(motion)

                    elif motion.type == "homing":
                        reset_setpos = True
                    elif motion.type == "limit_search":
                        reset_setpos = True

                if reset_setpos:
                    # even in case of interrupted motion, monitoring tasks have been joined
                    # so dial and state cache have been updated after hw_state reported not READY
                    # so axis.position is the correct/up-to-date value read after axis has been stopped
                    # (if motor controller stop-cmd returns once the motor has really stopped!!!)
                    with motion_bench("reset set_positions"):
                        for motion in self.motions_iter:

                            with capture():
                                motion.axis._set_position = motion.axis.position
                                event.send(motion.axis, "sync_hard")

                with capture():
                    self._parallel_set_move_done()

                with capture():
                    if self.parent:
                        _emit_move_done(self.parent)

                if self._interrupted_move:
                    for motion in self.motions_iter:

                        with capture():
                            _axis = motion.axis
                            _axis_pos = safe_get(_axis, "position", on_error="!ERR")
                            _axis_pos = _axis.axis_rounder(_axis_pos)
                            msg = f"Axis {_axis.name} stopped at position {_axis_pos}"
                            event.send_safe(
                                _axis,
                                "msg",
                                msg,
                            )
                            if motion.type != "move":
                                print(msg)

                # once move task is finished, check encoder if needed
                with motion_bench("do_encoder_reading"):
                    for axis in (m.axis for m in all_motions):

                        with capture():
                            if axis._check_encoder:
                                axis._do_encoder_reading()

                for _, err, _ in capture.exception_infos:
                    _motion_logger.info(f"ERROR: cleanup: {err}")
