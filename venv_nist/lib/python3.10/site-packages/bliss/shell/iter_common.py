# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Iterator to format data for common BLISS objects
"""
from __future__ import annotations
from typing import NamedTuple, Any
from collections.abc import Iterator

import functools
import fnmatch

from bliss import global_map, current_session
from bliss.common.axis.axis import Axis
from bliss.common.axis.state import AxisState
from bliss.common import measurementgroup

from bliss.common.utils import safe_get, ErrorWithTraceback

_ERR = "!ERR"


class WhereAll(NamedTuple):
    axis: Axis
    disabled: bool
    error: ErrorWithTraceback
    unit: str | None
    user_position: float
    dial_position: float


class WhereMotor(NamedTuple):
    axis: Axis
    disabled: bool
    error: ErrorWithTraceback | None
    unit: str | None
    user_position: float
    user_high_limit: float
    user_low_limit: float
    offset: float
    dial_position: float
    dial_high_limit: float
    dial_low_limit: float


class StateMotor(NamedTuple):
    axis_name: str
    state: AxisState | ErrorWithTraceback


class CountersList(NamedTuple):
    fullname: str
    shape: str
    prefix: str
    name: str
    alias: str | None


def iter_counters(counters=None) -> Iterator[CountersList]:
    """
    Return a dict of counters
    """
    counters_dict = dict()
    shapes = ["0D", "1D", "2D", "3D"]
    if counters is None:
        counters = global_map.get_counters_iter()

    for cnt in counters:
        prefix, _, short_name = cnt.fullname.rpartition(":")
        counters_dict[cnt.fullname] = (
            shapes[len(cnt.shape)],
            cnt._counter_controller.name if cnt._counter_controller else prefix,
            short_name,
            getattr(cnt, "original_name", None),
        )
    for fullname, (shape, prefix, name, alias) in counters_dict.items():
        yield CountersList(fullname, shape, prefix, name, alias)


def iter_axes_state(
    *axes: list[str | Axis], read_hw: bool = False
) -> Iterator[StateMotor]:
    """
    Returns state information of the given axes

    Arguments:
        axis: motor axis
        read_hw: If True, force communication with hardware, otherwise
                        (default) use cached value.
    """
    for axis in global_map.get_axis_objects_iter(*axes):
        if axis.name not in current_session.env_dict:
            continue
        state = safe_get(
            axis, "state", on_error=ErrorWithTraceback(error_txt=_ERR), read_hw=read_hw
        )
        yield StateMotor(global_map.alias_or_name(axis), state)


def iter_axes_state_all(read_hw: bool = False) -> Iterator[StateMotor]:
    """
    Returns state information about all axes

    Arguments:
        read_hw: If True, force communication with hardware, otherwise
                 (default) use cached value.
    """
    return iter_axes_state(*list(global_map.get_axes_iter()), read_hw=read_hw)


def iter_axes_position_all(err: str = _ERR) -> Iterator[WhereAll]:
    """
    Iterates all positions (Where All) in both user and dial units

    Arguments:
        err: Replacement string for unreadable position
    """
    for (
        axis,
        disabled,
        error,
        user_position,
        dial_position,
        unit,
    ) in global_map.get_axes_positions_iter(on_error=ErrorWithTraceback(error_txt=err)):
        if axis.name not in current_session.env_dict:
            continue
        yield WhereAll(axis, disabled, error, unit, user_position, dial_position)


def iter_axes_position(*axes: list[Axis], err: str = _ERR) -> Iterator[WhereMotor]:
    """
    Return information (position - user and dial, limits) of the given axes

    Arguments:
        axis: motor axis
        err: Replacement string for unreadable position
    """
    if not axes:
        raise RuntimeError("need at least one axis name/object")

    safe_get_traceback = functools.partial(
        safe_get, on_error=ErrorWithTraceback(error_txt=err)
    )

    for axis in global_map.get_axis_objects_iter(*axes):
        user_low_limit, user_high_limit = safe_get(axis, "limits", on_error=(err, err))
        disabled_state = axis.disabled
        error: ErrorWithTraceback | None = None
        user_position = dial_position = float("nan")
        if not disabled_state:
            user_position = safe_get_traceback(axis, "position")
            if isinstance(user_position, ErrorWithTraceback):
                error = user_position
                user_position = float("nan")
            else:
                dial_position = safe_get_traceback(axis, "dial")
                if isinstance(dial_position, ErrorWithTraceback):
                    error = dial_position
                    dial_position = float("nan")
        offset = safe_get(axis, "offset", on_error=float("nan"))
        dial_low_limit, dial_high_limit = safe_get(
            axis, "dial_limits", on_error=(err, err)
        )
        unit = axis.config.get("unit", default=None)

        where_motor = WhereMotor(
            axis,
            disabled_state,
            error,
            unit,
            user_position,
            user_high_limit,
            user_low_limit,
            offset,
            dial_position,
            dial_high_limit,
            dial_low_limit,
        )

        yield where_motor


def list_obj(pattern=None) -> list[Any]:
    """
    Used by bliss.shell.standard.lsobj()
    """
    obj_list = list()

    if pattern is None:
        pattern = "*"

    # Names of objects found in session
    for name in current_session.object_names:
        if fnmatch.fnmatch(name, pattern):
            # check if an object is an aliased object.
            try:
                # "name" is aliased -> add it's alias name.
                name = global_map.alias_or_name(name)
            except AttributeError:
                # "name" is not aliased -> add it.
                pass
            obj_list.append(name)

    # Add aliases (some are not in config-objects)
    for name in global_map.aliases.names_iter():
        if fnmatch.fnmatch(name, pattern):
            obj_list.append(name)

    return obj_list


def list_motors() -> list[str]:
    """
    Return list of motors configured in current session

    Used by bliss.shell.standard.lsmot()
    """
    motor_list: list[str] = list()
    for name in global_map.get_axes_names_iter():
        motor_list.append(name)

    return motor_list


def list_mg() -> str:
    """
    Return the list of measurment groups
    Indicate the current active one with a star char: '*'
    """
    active_mg_name = measurementgroup.get_active_name()
    lsmg_str = ""

    for mg_name in measurementgroup.get_all_names():
        if mg_name == active_mg_name:
            lsmg_str += f" * {mg_name}\n"
        else:
            lsmg_str += f"   {mg_name}\n"

    return lsmg_str
