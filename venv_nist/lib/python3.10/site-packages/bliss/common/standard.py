# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Standard bliss macros (:func:`~bliss.common.standard.wa`, \
:func:`~bliss.common.standard.mv`, etc)
"""
from __future__ import annotations

import contextlib
import gevent
import sys
import collections

from bliss import global_map, global_log, current_session  # noqa: F401
from bliss.common import event
from bliss.common import scans
from bliss.common.protocols import HasInfo
from bliss.common.scans import *  # noqa: F401,F403
from bliss.common.plot import plot
from bliss.common.soft_axis import SoftAxis
from bliss.common.axis.axis import Axis as _Axis
from bliss.common.motor_group import _Group
from bliss.common.counter import SoftCounter
from bliss.common.cleanup import cleanup, error_cleanup
from bliss.common import cleanup as cleanup_mod
from bliss.common.interlocks import interlock_state  # noqa: F401
from bliss.controllers.motors import esrf_undulator
from bliss.config.channels import clear_cache
from bliss.common.types import (
    _scannable_position_list_or_group_position_list,
    Scannable,
    _float,
)
from bliss.common.utils import (  # noqa: F401
    grouped,
    typecheck_var_args_pattern,
    modify_annotations,
    custom_error_msg,
    shorten_signature,
)


__all__ = (
    [
        "mv",
        "mvr",
        "mvd",
        "mvdr",
        "move",
        "sync",
        "interlock_state",
        "reset_equipment",
    ]
    + scans.__all__
    + ["cleanup", "error_cleanup", "plot"]
    + ["SoftAxis", "SoftCounter"]
)


from bliss.common.motor_group import Group, is_motor_group


def sync(*axes: list[str | _Axis]):
    """
    Forces axes synchronization with the hardware

    Args:
        axes: list of axis objects or names. If no axis is given, it syncs all
              all axes present in the session
    """
    print("Forcing axes synchronization with hardware")
    if axes:
        axis_list = global_map.get_axis_objects_iter(*axes)
    else:
        axis_list = global_map.get_axes_iter()

    for axis in axis_list:
        try:
            axis.sync_hard()
        except Exception as exc:
            try:
                raise RuntimeError(
                    f"Synchronization error for axis '{axis.name}'"
                ) from exc
            except Exception:
                sys.excepthook(*sys.exc_info())


@custom_error_msg(
    TypeError,
    "intended usage: mv(motor1, target_position_1, motor2, target_position_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, pos1, motor2, pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
def mv(*args):
    """
    Moves given axes to given absolute positions

    Arguments are interleaved axis and respective absolute target position.
    Example::

        >>> mv(th, 180, chi, 90)

    See Also: move
    """
    motion_group = _parse_motion_group(args)
    _move(motion_group=motion_group)


@custom_error_msg(
    TypeError,
    "intended usage: mvr(motor1, rel_target_position_1, motor2, rel_target_position_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, r_pos1, motor2, r_pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
def mvr(*args):
    """
    Moves given axes to given relative positions

    Arguments are interleaved axis and respective relative target position.
    Example::

        >>> mv(th, 180, chi, 90)
    """
    motion_group = _parse_motion_group(args)
    _move(motion_group=motion_group, relative=True)


@custom_error_msg(
    TypeError,
    "intended usage: mvd(motor1, target_position_1, motor2, target_position_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, pos1, motor2, pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
def mvd(*args):
    """
    Moves given axes to given absolute dial positions

    Arguments are interleaved axis and respective relative target position.
    """
    motion_group = _parse_motion_group(args)
    _move(motion_group=motion_group, dial=True)


@custom_error_msg(
    TypeError,
    "intended usage: mvdr(motor1, rel_target_position_1, motor2, rel_target_position_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, r_pos1, motor2, r_pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
def mvdr(*args):
    """
    Moves given axes to given relative dial positions

    Arguments are interleaved axis and respective relative target position.
    """
    motion_group = _parse_motion_group(args)
    _move(motion_group=motion_group, relative=True, dial=True)


@custom_error_msg(
    TypeError,
    "intended usage: move(motor1, target_position_1, motor2, target_position_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, pos1, motor2, pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
def move(
    *args,
    wait: bool = True,
    relative: bool = False,
    dial: bool = False,
    print_motion: bool = True,
    display_dependencies: bool = False,
):
    """
    Moves given axes to given absolute positions

    Arguments are interleaved axis and respective absolute target position.
    Example::

        >>> mv(th, 180, chi, 90)

    See Also: mv

    Arguments:
        axis: List of axis object and target positions
        wait: If true, the call is blocking until the end of the move
        relative: If true, the target positiob are relative to the actual
                  axis position
        dial: If true, the the target position is for the dial position
        print_motion: If true, the listing of the motion is print before
                      processing
        display_dependencies: If true, include to the display the dependency axis
            used by the requested axis. If false only the requested axis are displayed
    """
    motion_group = _parse_motion_group(args)
    _move(
        motion_group=motion_group,
        relative=relative,
        dial=dial,
        wait=wait,
        print_motion=print_motion,
        display_dependencies=display_dependencies,
    )


def _parse_motion_group(
    axis_pos_list: _scannable_position_list_or_group_position_list,
) -> dict[_Axis, float]:
    """Normalize a list of interleaved axes and positions into a motion dict.

    This interleaved structure is mostly used to simplify typing og mv/umv...
    shell commands.
    """
    motion_group: dict[_Axis, float]
    if isinstance(axis_pos_list, (dict, collections.UserDict)):
        motion_group = dict(axis_pos_list)
    elif len(axis_pos_list) == 2 and is_motor_group(axis_pos_list[0]):
        motion_group = {}
        group: _Group = axis_pos_list[0]
        pos_list = axis_pos_list[1]
        for a, p in zip(group._axes, pos_list):
            motion_group[a] = p
    else:
        motion_group = {}
        for a, p in list(grouped(axis_pos_list, 2)):
            motion_group[a] = p

    return motion_group


def _move(
    motion_group: dict[_Axis, float],
    relative: bool = False,
    dial: bool = False,
    wait: bool = True,
    print_motion: bool = True,
    display_dependencies: bool = False,
):
    """
    Move a group at it's target position.

    Arguments:
        motion_group: A motion group containing mapping from axis to absolute user positions
        wait: If true, the call is blocking until the end of the move
        relative: If true, the target positiob are relative to the actual
                  axis position
        dial: If true, the the target position is for the dial position
        print_motion: If true, the listing of the motion is print before
                      processing
        display_dependencies: If true, include to the display the dependency axis
            used by the requested axis. If false only the requested axis are displayed
    """
    group = Group(*motion_group.keys())

    # normalize positions as user positions
    for m, p in motion_group.items():
        if relative:
            motion_group[m] = p * m.sign if dial else p
        else:
            motion_group[m] = m.dial2user(p) if dial else p

    def display_msg(msg):
        print(msg)

    displayed_axes: list[_Axis] = []
    if print_motion:
        if display_dependencies:
            displayed_axes = list(group.axes_with_reals.values())
        else:
            displayed_axes = list(motion_group.keys())
    try:
        for m in displayed_axes:
            event.connect(m, "msg", display_msg)
        group.move(motion_group, wait=wait, relative=relative)
    finally:
        for m in displayed_axes:
            try:
                event.disconnect(m, "msg", display_msg)
            except Exception:
                # In case an interruption was occured during the connect
                pass

    return group, motion_group


def info(obj):
    """
    In Bliss `__info__` is used by the command line interface (Bliss shell or
    Bliss repl) to enquire information of the internal state of any object /
    controller in case it is available. this info function is to be seen as
    equivalent of str(obj) or repr(obj) in this context.

    if *obj* has `__info__` implemented this `__info__` function will be
    called. As a fallback option (`__info__` not implemented) repr(obj) is used.
    """
    from prompt_toolkit import ANSI, HTML
    from prompt_toolkit.formatted_text import FormattedText

    if isinstance(obj, HasInfo):
        info_str = obj.__info__()
        if not isinstance(info_str, (str, ANSI, HTML, FormattedText)):
            raise TypeError("__info__ must return a str/ANSI/HTML/FormattedText")
    else:
        info_str = repr(obj)

    return info_str


def wid():
    """
    Print the list of undulators defined in the session and their positions.
    Initialize axes if not already done.
    Print all axes of the ID device server.
    """

    # Get list of ID_DS objects (not undulators)
    # Usually 1 ID_DS per beamline for many undulators.
    ID_DS_list = esrf_undulator.get_all()
    undu_str = ""
    for id_ds in ID_DS_list:
        undu_str += "\n    ---------------------------------------\n"
        undu_str += f"    ID Device Server {id_ds.ds_name}\n"

        power = f"{id_ds.device.Power:.3f}"
        max_power = f"{id_ds.device.MaxPower:.1f}"
        power_density = f"{id_ds.device.PowerDensity:.3f}"
        max_power_density = f"{id_ds.device.MaxPowerDensity:.1f}"

        undu_str += f"            Power: {power} /  {max_power}  kW\n"
        undu_str += (
            f"    Power density: {power_density} / {max_power_density}  kW/mr2\n\n"
        )

        for undu_axis_name, undu_axis in id_ds.axes.items():
            # .state initializes axes if not already done.
            uaxis_state = undu_axis.state
            uinfo = undu_axis.controller.axis_info[undu_axis]

            if uinfo["is_revolver"]:
                undu_type = " - Revolver"
            else:
                undu_type = " "

            able = "DISABLED" if "DISABLED" in uaxis_state else "ENABLED"
            upos = (
                "          " if able == "DISABLED" else f"GAP:{undu_axis.position:5.3f}"
            )
            undu_str += (
                f"    {undu_axis.name:<10} - {upos:<9} - {able:<8} - {undu_type}\n"
            )

    return undu_str


@contextlib.contextmanager
def rockit(motor, total_move):
    """
    Rock an axis from it's current position +/- total_move/2.
    Usage example:

    .. code-block:: python

        with rockit(mot1, 10):
             ascan(mot2,-1,1,10,0.1,diode)
             amesh(....)
    """
    if motor.is_moving:
        raise RuntimeError(f"Motor {motor.name} is moving")

    lower_position = motor.position - (total_move / 2)
    upper_position = motor.position + (total_move / 2)
    # Check limits
    motor._get_motion(lower_position)
    motor._get_motion(upper_position)

    def rock():
        while True:
            motor.move(lower_position)
            motor.move(upper_position)

    with cleanup_mod.cleanup(motor, restore_list=(cleanup_mod.axis.POS,)):
        rock_task = gevent.spawn(rock)
        try:
            yield
        finally:
            rock_task.kill()
            rock_task.get()


def reset_equipment(*devices):
    """
    This command will force all devices passed as argument to be reset
    For now we just force an re-initialization on next call.
    """
    device_to_reset = set()
    for dev in devices:
        device_to_reset.add(dev)
        try:
            ctrl = dev.controller
        except AttributeError:
            pass
        else:
            device_to_reset.add(ctrl)
    # clear controller cache
    clear_cache(*device_to_reset)
    # Maybe in future it'll be good to close the connection and do other things...
