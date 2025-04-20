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

import itertools
import shutil
import sys
import typing
import numpy
import prompt_toolkit
from bliss import global_map
from bliss import current_session
from bliss.common.standard import wid as std_wid
from bliss.common.utils import (
    typecheck,
    chunk_list,
    custom_error_msg,
    shorten_signature,
)
from bliss.shell import iter_common as _iter_common
import tabulate
from bliss.shell.formatters import tabulate as _bliss_tabulate
from bliss.common.types import _scannable_or_name
from bliss.common.protocols import CounterContainer
from bliss.common.counter import Counter
from ._utils import print_html
from bliss.shell.pt import default_style
from bliss.common.axis.state import AxisState
from bliss.common.utils import ErrorWithTraceback
from bliss.scanning.scan_display import ScanDisplay


# Expose this functions from this module
from bliss.common.standard import sync  # noqa: E402,F401
from bliss.common.standard import reset_equipment  # noqa: E402,F401
from ..interlocks import interlock_state  # noqa: E402,F401


tabulate.PRESERVE_WHITESPACE = True

_ERR = "!ERR"
_DIS = "*DIS*"
_DISABLED = "*DISABLED*"
_MISSING_VAL = "-----"


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


@typecheck
def lscnt(counter_container: typing.Union[CounterContainer, Counter, None] = None):
    """
    Display the list of all counters, sorted alphabetically
    """
    if counter_container is None:
        counters = None
    elif isinstance(counter_container, CounterContainer):
        counters = counter_container.counters
    else:
        # must be Counter
        counters = [counter_container]

    table_info = []
    for counter_name, shape, prefix, name, alias in sorted(
        _iter_common.iter_counters(counters)
    ):
        if alias:
            alias = "      *"
        table_info.append(itertools.chain([counter_name], (shape, prefix, alias, name)))
    print("")
    print(
        str(
            tabulate.tabulate(
                table_info, headers=["Fullname", "Shape", "Controller", "Alias", "Name"]
            )
        )
    )


def lsmg():
    """
    Print the list of measurment groups
    Indicate the current active one with a star char: '*'
    """
    print(_iter_common.list_mg())


def lsobj(pattern=None):
    """
    Print the list of BLISS object in current session matching the
    <pattern> string.
    <pattern> can contain jocker characters like '*' or '?'.
    NB: print also badly initilized objects...
    """

    for obj_name in _iter_common.list_obj(pattern):
        print(obj_name, end="  ")

    print("")


def wid():
    """
    Print the list of undulators defined in the session
    and their positions.
    Print all axes of the ID device server.
    """
    print(std_wid())


@typecheck
def stm(*axes: _scannable_or_name, read_hw: bool = False):
    """
    Display state information of the given axes

    Args:
        axis (~bliss.common.axis.Axis): motor axis

    Keyword Args:
        read_hw (bool): If True, force communication with hardware, otherwise
                        (default) use cached value.
    """
    data = _iter_common.iter_axes_state(*axes, read_hw=read_hw)

    table = [(axis, state) for (axis, state) in data]

    print(str(tabulate.tabulate([("Axis", "Status")] + table, headers="firstrow")))

    errors = []
    for label, state in table:
        if str(state) == _ERR:
            errors.append((label, state))

    _print_error_reports(errors, device_type="motor")


@typecheck
def sta(read_hw: bool = False):
    """
    Return state information about all axes

    Keyword Args:
        read_hw (bool): If True, force communication with hardware, otherwise
                        (default) use cached value.
    """
    return stm(*list(global_map.get_axes_iter()), read_hw=read_hw)


def _print_formatted_text(formatted_text):
    """Remove the '\r' to make it usable by BLISS.

    It's a work around for now. See https://gitlab.esrf.fr/bliss/bliss/-/issues/4151
    """
    prompt_toolkit.print_formatted_text(
        formatted_text,
        output=current_session.output,
        style=default_style.get_style(),
    )


def wa(max_cols: int | None = None, show_dial: bool = True):
    """
    Display all positions (Where All) in both user and dial units.

    Arguments:
        max_cols: Max number of columns to display
        show_dial: If true (default) also show the dial position in the list
    """

    header: list = []
    pos: list = []
    dial: list = []
    tables: list = []
    errors = []

    if max_cols is None:
        scan_display = ScanDisplay()
        max_cols = scan_display.wa_max_cols

    axes_and_pos = list(_iter_common.iter_axes_position_all())
    if len(axes_and_pos) == 0:
        print("No motor defined in session")
        return

    for info in axes_and_pos:
        axis = info.axis
        if len(tables) == 0 or len(header) == max_cols:
            # Blank line
            if not len(tables) == 0:
                tables.append([])
            header, pos, dial = [], [], []
            tables.extend((header, pos))
            if show_dial:
                tables.append(dial)

        if len(header) == 0:
            header.append("")
            pos.append("user")
            if show_dial:
                dial.append("dial")

        axis_label = axis.name
        if axis.unit:
            axis_label += "[{0}]".format(axis.unit)

        header.append(("class:header", axis_label))

        f_position: str | float
        f_dial_position: str | float
        if info.disabled:
            f_position = f_dial_position = _DIS
            style = "class:warning"
        elif info.error:
            errors.append((axis.name, info.error))
            f_position = f_dial_position = _ERR
            style = "class:danger"
        else:
            try:
                state = axis.state
            except Exception as e:
                error = ErrorWithTraceback()
                error.exc_info = sys.exc_info()
                error.exception = e
                errors.append((axis.name, error))
                state = AxisState("FAULT")

            if state.DISABLED:
                style = "class:warning"
            elif state.LIMNEG:
                style = "class:warning"
            elif state.LIMPOS:
                style = "class:warning"
            elif state.FAULT:
                style = "class:danger"
            else:
                style = ""
            if state.DISABLED and numpy.isnan(info.user_position):
                # Some equipments like revolver undulator use a neutral position
                f_position = f_dial_position = _DIS
            else:
                f_position = axis.axis_rounder(info.user_position)
                f_dial_position = axis.axis_rounder(info.dial_position)

        pos.append((style, f_position))
        dial.append((style, f_dial_position))

    _print_formatted_text(
        _bliss_tabulate.tabulate(
            tables,
            tablefmt="plain",
            stralign="right",
        ),
    )

    _print_error_reports(errors, device_type="motor")


def wu(max_cols: int | None = None, show_dial: bool = True):
    """
    Display all positions (Where Users) in user units.

    Arguments:
        max_cols: Max number of columns to display
        show_dial: If true (default) also show the dial position in the list
    """
    wa(show_dial=False, max_cols=max_cols)


def lsmot():
    """
    Display names of motors configured in current session.
    """

    motor_list = _iter_common.list_motors()

    # Maximal length of objects names (min 5).
    display_width = shutil.get_terminal_size().columns
    if len(motor_list) == 0:
        max_length = 5
        print("No motor found in current session's config.")
    else:
        max_length = max([len(x) for x in motor_list])

        # Number of items displayable on one line.
        item_number = int(display_width / max_length) + 1

        motor_list.sort(key=str.casefold)

        print("Motors configured in current session:")
        print("-------------------------------------")
        print(tabulate.tabulate(chunk_list(motor_list, item_number), tablefmt="plain"))
        print("\n")


@custom_error_msg(
    TypeError,
    "intended usage: wm(axis1, axis2, ... ) Hint:",
    display_original_msg=True,
)
@shorten_signature(annotations={"axes": "axis1, axis2, ... "}, hidden_kwargs=[])
@typecheck
def wm(*axes: _scannable_or_name, max_cols=None):
    """
    Display information (position - user and dial, limits) of the given axes.

    Arguments:
        axis: A motor axis
        max_cols: Max number of columns to display

    Example:

    >>> wm(m2, m1, m3)

    .. code-block::

                            m2      m1[mm]       m3
          --------  ----------  ----------  -------
          User
           High     -123.00000   128.00000      inf
           Current   -12.00000     7.00000  3.00000
           Low       456.00000  -451.00000     -inf
          Offset       0.00000     3.00000  0.00000
          Dial
           High      123.00000   123.00000      inf
           Current    12.00000     2.00000  3.00000
           Low      -456.00000  -456.00000     -inf
    """
    if not axes:
        print(
            "wm() needs at least one axis name/object as parameter.\n"
            "example: wm(mot1)\n"
            "         wm(mot1, mot2, ... motN)"
        )
        return

    err = _ERR

    errors = []
    header = [""]
    User, high_user, user, low_user = (
        ["User~~~~"],
        ["~High~~~"],
        ["~Current"],
        ["~Low~~~~"],
    )
    Dial, high_dial, dial, low_dial = (
        ["Dial~~~~"],
        ["~High~~~"],
        ["~Current"],
        ["~Low~~~~"],
    )
    Offset, Spacer = ["Offset~~"], [""]
    tables = [
        (
            header,
            User,
            high_user,
            user,
            low_user,
            Offset,
            Spacer,
            Dial,
            high_dial,
            dial,
            low_dial,
        )
    ]

    if max_cols is None:
        scan_display = ScanDisplay()
        max_cols = scan_display.wa_max_cols

    for wm_info in _iter_common.iter_axes_position(*axes, err=err):

        if len(header) == max_cols:
            header = [None]
            User, high_user, user, low_user = (
                ["User~~~~"],
                ["~High~~~"],
                ["~Current"],
                ["~Low~~~~"],
            )
            Dial, high_dial, dial, low_dial = (
                ["Dial~~~~"],
                ["~High~~~"],
                ["~Current"],
                ["~Low~~~~"],
            )
            Offset = ["Offset~~"]
            tables.append(
                (
                    header,
                    User,
                    high_user,
                    user,
                    low_user,
                    Offset,
                    Spacer,
                    Dial,
                    high_dial,
                    dial,
                    low_dial,
                )
            )
        axis_label = wm_info.axis.name
        if wm_info.unit:
            axis_label += "[{0}]".format(wm_info.unit)

        if wm_info.user_high_limit not in (None, err):
            user_high_limit = wm_info.axis.axis_rounder(wm_info.user_high_limit)
            dial_high_limit = wm_info.axis.axis_rounder(wm_info.dial_high_limit)
        else:
            user_high_limit = dial_high_limit = _MISSING_VAL

        if wm_info.user_low_limit not in (None, err):
            user_low_limit = wm_info.axis.axis_rounder(wm_info.user_low_limit)
            dial_low_limit = wm_info.axis.axis_rounder(wm_info.dial_low_limit)
        else:
            user_low_limit = dial_low_limit = _MISSING_VAL

        high_user.append(user_high_limit)
        user_position = wm_info.user_position
        dial_position = wm_info.dial_position
        if wm_info.error:
            errors.append((wm_info.axis.name, wm_info.error))
            user_position = dial_position = _ERR
        elif wm_info.disabled:
            axis_label += f" {_DISABLED}"
        else:
            state = wm_info.axis.state
            if state.DISABLED and numpy.isnan(wm_info.user_position):
                # Some equipments like revolver undulator use a neutral position
                user_position = dial_position = _DISABLED
        user_position = wm_info.axis.axis_rounder(user_position)
        dial_position = wm_info.axis.axis_rounder(dial_position)
        header.append(axis_label)
        User.append(None)
        user.append(user_position)
        low_user.append(user_low_limit)
        Dial.append(None)
        high_dial.append(dial_high_limit)
        dial.append(dial_position)
        low_dial.append(dial_low_limit)
        offset = wm_info.axis.axis_rounder(wm_info.offset)
        Offset.append(offset)

    for table in tables:
        print("")
        print(
            str(
                tabulate.tabulate(
                    table, disable_numparse=True, headers="firstrow", stralign="right"
                )
            ).replace("~", " ")
        )

    _print_error_reports(errors, device_type="motor")


def interlock_show(wago_obj=None):
    """
    Display interlocks configuration on given Wago object (if given)
    or display configuration of all known Wagos
    """
    if wago_obj:
        wago_obj.interlock_show()
    else:
        try:
            wago_instance_list = tuple(
                global_map[id_]["instance"]()
                for id_ in global_map.find_children("wago")
            )
        except TypeError:
            print("No Wago found")
            return
        names = [wago.name for wago in wago_instance_list]
        print_html(
            f"Currently configured Wagos: <color1>{' '.join(names)}</color1>\n\n"
        )
        for wago in wago_instance_list:
            wago.interlock_show()
