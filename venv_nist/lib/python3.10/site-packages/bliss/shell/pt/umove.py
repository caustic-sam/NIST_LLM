# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Move with user feedback.
"""

from __future__ import annotations
from typing import NamedTuple

import logging
import numpy
import shutil

from bliss.common.utils import grouped_with_tail
from bliss import global_map
from bliss.common.standard import _move
from bliss.common.axis.axis import Axis
from bliss.common.axis.state import AxisState
from bliss.common.motor_group import _Group
from bliss.common.cleanup import error_cleanup
from prompt_toolkit.formatted_text import FormattedText
from . import layout_helper


_logger = logging.getLogger(__name__)


def layout_umv_in_terminal(
    nb_axis: int,
    cell_width: int,
    spacing: int,
    max_row_cells: int | None,
    column_header_size: int | None = None,
) -> tuple[int, int]:
    """Returns the amount of columns and rows to display umv in the terminal.

    It is based on the actual width of the terminal.

    It contains specific business logic.
    """
    columns, _rows = shutil.get_terminal_size((80, 20))
    if column_header_size:
        columns -= column_header_size
    max_column_cells = layout_helper.layout_box(columns, cell_width, spacing)
    if max_row_cells is None:
        max_row_cells = layout_helper.layout_grid_flow_min_size(
            nb_axis, max_column_cells
        )
        if nb_axis > 10:
            max_row_cells = min(max_row_cells, 2)
    columns, rows = layout_helper.layout_packed_grid(
        nb_axis, max_column_cells, max_row_cells
    )
    return columns, rows


def display_digits(axis: Axis) -> int:
    try:
        # BLISS 2.0
        return axis.display_digits
    except Exception:
        pass

    try:
        return int(numpy.ceil(-numpy.log10(axis.tolerance)))
    except ZeroDivisionError:
        return 0


def _max_value_length(axis: Axis) -> int:
    """Returns the max size the axis value can take"""
    umin, umax = axis.limits
    dmin, dmax = axis.dial_limits

    def integer_digits(value):
        """Measure the size of the integer + negsign"""
        text = f"{value:f}"
        return text.find(".")

    values = [umin, umax, dmin, dmax]
    values = [integer_digits(v) for v in values]
    max_size = max(values)

    digits = display_digits(axis)
    if digits != 0:
        max_size += digits + 1  # the dot and the digits

    return max_size


class _AxisDesc(NamedTuple):
    label: str
    axis: Axis
    value_format: str
    user: float | None
    dial: float | None
    target: float
    state: AxisState | None


class _UserMoveFormatter:
    """Hold the state of axis to be displayed"""

    def __init__(self, group: _Group, axes: list[Axis]):
        self._group = group
        self._axes = axes

        self._axes_label: dict[Axis, str] = {}
        self._axes_target: dict[Axis, float] = {}
        self._axes_value_format: dict[Axis, str] = {}

        self._display_dials = False
        """If true, display dial position in the table"""

        max_value_len: list[int] = []

        for axis in axes:
            display_name = global_map.alias_or_name(axis)
            if axis.unit:
                display_name += f"[{axis.unit}]"

            self._axes_label[axis] = display_name
            max_value_len.append(_max_value_length(axis))
            self._axes_target[axis] = axis._set_position

        max_value_len.append(max([len(n) for n in self._axes_label.values()]))
        self._cell_width = max(max(max_value_len), 8)

        for axis in axes:
            self._axes_value_format[
                axis
            ] = f">{self._cell_width}.0{display_digits(axis)}f"

        self._label_format = f">{self._cell_width}"
        self._separator = "  "

    def _render_users(self, cells: list[_AxisDesc], sep=" ") -> list[tuple[str, str]]:
        """Render a row with axis user position"""

        def format_cell(cell: _AxisDesc):
            state = cell.state
            if state is None:
                classes = ""
            elif state.MOVING:
                classes = "class:info"
            elif state.FAULT:
                classes = "class:danger"
            elif state.READY:
                classes = ""
            else:
                classes = ""
            if cell.user is None:
                return (classes, format("None", f">{self._cell_width}s"))
            return (classes, format(cell.user, cell.value_format))

        result: list[tuple[str, str]] = []
        result.append(("", "user   "))
        for c in cells:
            if result != []:
                result.append(("", sep))
            result.append(format_cell(c))

        return result

    def _render_dials(self, cells: list[_AxisDesc], sep=" ") -> list[tuple[str, str]]:
        """Render a row with axis dial position"""
        result = []
        result.append(("", "dial   "))

        def format_dial(cell: _AxisDesc):
            if cell.dial is None:
                return format("None", f">{self._cell_width}s")
            return format(cell.dial, cell.value_format)

        line = sep.join([format_dial(c) for c in cells])
        result.append(("", line))
        return result

    def _render_targets(self, cells: list[_AxisDesc], sep=" ") -> list[tuple[str, str]]:
        """Render a row with target positions"""
        result: list[tuple[str, str]] = []
        result.append(("", "target "))
        for c in cells:
            if result != []:
                result.append(("", sep))
            result.append(("", format(c.target, c.value_format)))

        return result

    def _render_labels(self, cells: list[_AxisDesc], sep=" ") -> list[tuple[str, str]]:
        """Render a row with axis label"""

        def format_label(cell: _AxisDesc):
            return ("class:header", format(cell.label, self._label_format))

        result: list[tuple[str, str]] = []
        result.append(("", "       "))
        for c in cells:
            if result != []:
                result.append(("", sep))
            result.append(format_label(c))

        return result

    def render(self) -> tuple[int, str | FormattedText]:
        cell_columns, cell_rows = layout_umv_in_terminal(
            len(self._axes),
            self._cell_width,
            len(self._separator),
            max_row_cells=None,
            column_header_size=7 + len(self._separator),
        )

        window_height = 4 * cell_rows
        if self._display_dials:
            window_height += cell_rows

        result: list[tuple[str, str]] = []
        for axes in grouped_with_tail(self._axes, cell_columns):

            def get_desc(a: Axis) -> _AxisDesc:
                # The axis properties `state`/`dial`/`position` are  not usable
                # in this context because:
                # - `state` early return with `MOVING` state
                # - Each call can do direct access to the hardware device
                state = a.settings._get_last_local("state")
                dial = a.settings._get_last_local("dial_position")
                user = a.settings._get_last_local("position")
                return _AxisDesc(
                    axis=a,
                    label=self._axes_label[a],
                    user=user,
                    dial=dial,
                    target=self._axes_target[a],
                    value_format=self._axes_value_format[a],
                    state=state,
                )

            cells = [get_desc(a) for a in axes]

            if result != []:
                result.append(("", "\n\n"))

            result.extend(self._render_labels(cells, sep=self._separator))
            result.append(("", "\n"))
            result.extend(self._render_users(cells, sep=self._separator))
            if self._display_dials:
                result.append(("", "\n"))
                result.extend(self._render_dials(cells, sep=self._separator))
            result.append(("", "\n"))
            result.extend(self._render_targets(cells, sep=self._separator))

        return window_height, FormattedText(result)


def umove(
    motion_group: dict[Axis, float],
    relative: bool = False,
    dial: bool = False,
    print_motion: bool = True,
    display_dependencies: bool = True,
):
    """Move axis with user feedback

    Arguments:
        motion_group: Mapping from axis and expected target position
        relative: If true, the target positiob are relative to the actual
                  axis position
        dial: If true, the the target position is for the dial position
        print_motion: If true, the listing of the motion is print before
                      processing it
        display_dependencies: If true, include to the display the dependency axis
            used by the requested axis. If false only the requested axis are displayed
    """
    group, _motor_pos = _move(
        motion_group,
        relative=relative,
        dial=dial,
        wait=False,
        print_motion=print_motion,
        display_dependencies=display_dependencies,
    )
    print("")
    if display_dependencies:
        axes = list(group.axes_with_reals.values())
    else:
        axes = list(motion_group.keys())

    formatter = _UserMoveFormatter(group, axes)

    from bliss.shell.standard import text_block

    with text_block(render=formatter.render):
        # Blocking move motors
        with error_cleanup(group.stop):
            group.wait_move()
