# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Standard functions provided to the BLISS shell.
"""

from __future__ import annotations

import typing
from collections.abc import Callable
import sys
from bliss.common.utils import (
    custom_error_msg,
    modify_annotations,
    typecheck_var_args_pattern,
    typecheck,
    shorten_signature,
)
from bliss.common.standard import _parse_motion_group
from bliss.scanning import scan_tools
from bliss import current_session
from bliss.common.types import (
    _countable,
    _float,
)
from bliss.scanning.scan import Scan
from bliss.common.protocols import Scannable
from bliss.shell.pt.umove import umove as _umove
from bliss.common.axis.axis import Axis
from bliss.common.standard import _move as _common_move

# Expose this functions from this module
from bliss.common.standard import mv, mvr, mvd, mvdr, rockit  # noqa: E402,F401
from bliss.shell.pt import utils as pt_utils
from bliss.scanning.scan_display import ScanDisplay


def _print_error_reports(errors, device_type):
    """
    Print error reports in the BLISS shell, if some
    """
    if len(errors) > 0:
        print()
        for label, error_with_traceback_obj in errors:
            exc_type, exc_val, exc_tb = error_with_traceback_obj.exc_info
            try:
                raise RuntimeError(
                    f"Error on {device_type} '{label}': {str(exc_val)}"
                ) from error_with_traceback_obj.exception
            except RuntimeError:
                current_session.error_report.display_exception(*sys.exc_info())


def _move(
    motion_group: dict[Axis, float],
    relative: bool = False,
    dial: bool = False,
    display_mode: str = "auto",
    wait: bool = True,
    display_dependencies: bool = True,
):
    """
    Move given axes to given absolute positions.

    This function extend `bliss.shell.standard.move` to use shell features.

    It provides an updated display of the motor(s) position(s) while it(they)
    is(are) moving.

    Arguments:
        motion_group: Mapping from axis and expected target position
        relative: If true, the target positiob are relative to the actual
                  axis position
        wait: If true, the motion is blocking and can be displayed during the motion
        dial: If true, the the target position is for the dial position
        display_mode: One of:
                      - `"auto"` (the default): displays the motion live if the
                        shell is available and the current greenlet can get the
                        ownership on it and the motion is blocking, else only
                        display some logs;
                      - `"print"`: the motion is only print at start;
                      - `"no"`: no display.
        display_dependencies: If true, include to the display the dependency axis
            used by the requested axis. If false only the requested axis are displayed
    """
    if display_mode == "auto":
        if not wait:
            # the shell can't be grabbed because the function will return
            display_mode = "print"
        else:
            if not pt_utils.can_use_text_block():
                display_mode = "print"

    if display_mode == "auto":
        scan_display = ScanDisplay()
        _umove(
            motion_group,
            relative=relative,
            dial=dial,
            print_motion=scan_display.umv_show_start_positions,
            display_dependencies=display_dependencies,
        )
    elif display_mode == "print":
        _common_move(
            motion_group,
            relative=relative,
            dial=dial,
            print_motion=True,
            wait=wait,
            display_dependencies=display_dependencies,
        )
    elif display_mode == "no":
        _common_move(
            motion_group,
            relative=relative,
            dial=dial,
            print_motion=False,
            wait=wait,
            display_dependencies=display_dependencies,
        )
    else:
        raise ValueError(f"display_mode '{display_mode}' is not supported")


@custom_error_msg(
    TypeError,
    "intended usage: umv(motor1, target_position_1, motor2, target_position_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, pos1, motor2, pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
def move(
    *args,
    relative: bool = False,
    dial: bool = False,
    wait: bool = True,
    display_mode: str = "auto",
    display_dependencies: bool = True,
):
    """
    Move given axes to given absolute positions.

    This function extend `bliss.shell.standard.move` to use shell features.

    It provides an updated display of the motor(s) position(s) while it(they)
    is(are) moving.

    Arguments:
        args: Interleaved axis and respective absolute target position.
        relative: If true, the target positiob are relative to the actual
                  axis position
        wait: If true, the motion is blocking and can be displayed during the motion
        dial: If true, the the target position is for the dial position
        display_mode: One of:
                      - `"auto"` (the default): displays the motion live if the
                        shell is available and the current greenlet can get the
                        ownership on it and the motion is blocking, else only
                        display some logs;
                      - `"print"`: the motion is only print at start;
                      - `"no"`: no display.
        display_dependencies: If true, include to the display the dependency axis
            used by the requested axis. If false only the requested axis are displayed
    """
    motion_group = _parse_motion_group(args)
    _move(
        motion_group,
        relative=relative,
        dial=dial,
        wait=wait,
        display_mode=display_mode,
        display_dependencies=display_dependencies,
    )


@custom_error_msg(
    TypeError,
    "intended usage: umv(motor1, target_position_1, motor2, target_position_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, pos1, motor2, pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
@shorten_signature(hidden_kwargs=["wait"])
def umv(*args, wait: bool = True):
    """
    Move given axes to given absolute positions providing updated display of
    the motor(s) position(s) while it(they) is(are) moving.

    Arguments are interleaved axis and respective absolute target position.
    """
    move(*args, wait=wait)


@custom_error_msg(
    TypeError,
    "intended usage: umvr(motor1, relative_displacement_1, motor2, relative_displacement_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, rel. pos1, motor2, rel. pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
@shorten_signature(hidden_kwargs=["wait"])
def umvr(*args, wait: bool = True):
    """
    Move given axes to given relative positions providing updated display of
    the motor(s) position(s) while it(they) is(are) moving.

    Arguments are interleaved axis and respective relative target position.
    """
    move(*args, wait=wait, relative=True)


@custom_error_msg(
    TypeError,
    "intended usage: umvd(motor1, target_position_1, motor2, target_position_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, pos1, motor2, pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
@shorten_signature(hidden_kwargs=["wait"])
def umvd(*args, wait: bool = True):
    """
    Move given axes to given absolute dial positions providing updated display of
    the motor(s) user position(s) while it(they) is(are) moving.

    Arguments are interleaved axis and respective absolute target position.
    """
    move(*args, wait=wait, dial=True)


@custom_error_msg(
    TypeError,
    "intended usage: umvdr(motor1, relative_displacement_1, motor2, relative_displacement_2, ... )",
    display_original_msg=False,
)
@modify_annotations({"args": "motor1, rel. pos1, motor2, rel. pos2, ..."})
@typecheck_var_args_pattern([Scannable, _float])
@shorten_signature(hidden_kwargs=["wait"])
def umvdr(*args, wait: bool = True):
    """
    Move given axes to given relative dial positions providing updated display of
    the motor(s) user position(s) while it(they) is(are) moving.

    Arguments are interleaved axis and respective relative target position.
    """
    move(*args, wait=wait, relative=True, dial=True)


@typecheck
@shorten_signature(hidden_kwargs=[])
def goto_cen(
    counter: typing.Optional[_countable] = None,
    axis: typing.Optional[Scannable] = None,
    scan: typing.Optional[Scan] = None,
):
    """
    Return the motor position corresponding to the center of the fwhm of the last scan.
    Move scanned motor to this value.
    If <counter> is not specified, use selected counter.

    Example: goto_cen()
    """
    return scan_tools.goto_cen(counter=counter, axis=axis, scan=scan, move=_move)


@typecheck
@shorten_signature(hidden_kwargs=[])
def goto_com(
    counter: typing.Optional[_countable] = None,
    axis: typing.Optional[Scannable] = None,
    scan: typing.Optional[Scan] = None,
):
    """
    Return center of mass of last scan according to <counter>.
    Move scanned motor to this value.
    If <counter> is not specified, use selected counter.

    Example: goto_com(diode2)
    """
    return scan_tools.goto_com(counter=counter, axis=axis, scan=scan, move=_move)


@typecheck
@shorten_signature(hidden_kwargs=[])
def goto_peak(
    counter: typing.Optional[_countable] = None,
    axis: typing.Optional[Scannable] = None,
    scan: typing.Optional[Scan] = None,
):
    """
    Return position of scanned motor at maximum of <counter> of last scan.
    Move scanned motor to this value.
    If <counter> is not specified, use selected counter.

    Example: goto_peak()
    """
    return scan_tools.goto_peak(counter=counter, axis=axis, scan=scan, move=_move)


@typecheck
@shorten_signature(hidden_kwargs=[])
def goto_min(
    counter: typing.Optional[_countable] = None,
    axis: typing.Optional[Scannable] = None,
    scan: typing.Optional[Scan] = None,
):
    """
    Return position of scanned motor at minimum of <counter> of last scan.
    Move scanned motor to this value.
    If <counter> is not specified, use selected counter.
    """
    return scan_tools.goto_min(counter=counter, axis=axis, scan=scan, move=_move)


@typecheck
def goto_custom(
    func: Callable[[typing.Any, typing.Any], float],
    counter: typing.Optional[_countable] = None,
    axis: typing.Optional[Scannable] = None,
    scan: typing.Optional[Scan] = None,
):
    return scan_tools.goto_custom(
        func=func, counter=counter, axis=axis, scan=scan, move=_move
    )


def goto_click(scatter=False, curve=False):
    """Move the motor displayed by Flint at the location clicked by the user.

    It supports both curves and scatters, based on the previous scan done by BLISS.

    - For a curve, the x-axis have to display a BLISS motor
    - For a scatter, both x and y axes have to be a BLISS motor

    If both `scatter` and `curve` are false (the default) the last scan is used
    to decide which plot have to be used.

    Arguments:
        scatter: If true, use the default scatter plot
        curve: If true, use the default scatter plot

    Raises:
        RuntimeError: If flint was not open or displayed plot was not properly setup.
    """
    return scan_tools.goto_click(scatter=scatter, curve=curve, move=_move)
