# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Alignment Helpers: cen peak com that interact with plotselect
and work outside of the context of a scan while interacting
with the last scan.
"""

from __future__ import annotations

import numpy
from numpy.typing import NDArray
import collections
import typing
from typing import Optional, Any, Union
from collections.abc import Callable
from bliss import current_session, global_map
from bliss.common.protocols import Scannable
from bliss.common.types import _countable
from bliss.common.plot import display_motor
from bliss.scanning.scan_display import ScanDisplay
from bliss.common.utils import shorten_signature, typecheck
from bliss.common.plot import get_flint
from bliss.common.logtools import log_warning
from bliss.common.motor_group import Group
from bliss.common.cleanup import error_cleanup, axis as cleanup_axis
from bliss.scanning import scan_math
from bliss.common.axis.axis import Axis
from bliss.scanning.scan import Scan


_DType = typing.TypeVar("_DType", bound=numpy.generic)
_Numeric1DArray = typing.Annotated[NDArray[_DType], typing.Literal["N"]]


class _NumericFitFunction(typing.Protocol):
    """Fit function returning a position on the axis"""

    def __call__(self, counter: _Numeric1DArray, axis: _Numeric1DArray) -> float:
        ...


class _CounterAxisFitFunction(typing.Protocol):
    """Fit function returning a position on the axis"""

    def __call__(
        self, counter: typing.Any | None, axis: Scannable | str | None
    ) -> float:
        ...


class _MotionDict(collections.UserDict):
    """Dictionary mapping from Axis to fitted position."""

    def __info__(self):
        """TODO: could be a nice table at one point"""
        s = "{"
        for key, value in self.items():
            if len(s) != 1:
                s += ", "
            if isinstance(key, str):
                s += f"{key}: {value}"
            else:
                s += f"{key.name}: {key.axis_rounder(value)}"
        s += "}"
        return s


class ScanFits:
    """Provides fit helper function from a scan.

    This class is supposed to be used as a programatic API.

    NOTE: It supports fitting on the time. But this is very specific
          to hardcoded known axis like "elapsed_time", "epoch". This
          probably dont work on fast scan.

    NOTE: Scan with multiple time base will probably fail to properly
          fit all the axes
    """

    _TIME_AXES: tuple[str, ...] = ("elapsed_time", "epoch")

    def __init__(self, scan: Scan):
        self._scan = scan

    def _get_x_y_data(self, counter, axis) -> tuple[_Numeric1DArray, _Numeric1DArray]:
        x_data = self._scan.streams[axis][:]
        y_data = self._scan.streams[counter][:]
        return x_data, y_data

    def fwhm(
        self, counter, axis: Scannable | str | None = None
    ) -> collections.UserDict[Scannable | str, float]:
        return self.compute_fit(self._fwhm, counter, axis)

    def _fwhm(self, counter, axis: Scannable | str | None = None):
        return scan_math.cen(*self._get_x_y_data(counter, axis))[1]

    def peak(
        self, counter, axis: Scannable | str | None = None
    ) -> collections.UserDict[Scannable | str, float]:
        return self.compute_fit(self._peak, counter, axis)

    def _peak(self, counter, axis: Scannable | str | None):
        return scan_math.peak(*self._get_x_y_data(counter, axis))

    def trough(
        self, counter, axis=None
    ) -> collections.UserDict[Scannable | str, float]:
        return self.compute_fit(self._trough, counter, axis)

    def _trough(self, counter, axis: Scannable | str | None):
        return scan_math.trough(*self._get_x_y_data(counter, axis))

    def com(
        self, counter, axis: Scannable | str | None = None
    ) -> collections.UserDict[Scannable | str, float]:
        return self.compute_fit(self._com, counter, axis)

    def _com(self, counter, axis: Scannable | str | None):
        return scan_math.com(*self._get_x_y_data(counter, axis))

    def cen(
        self, counter, axis: Scannable | str | None = None
    ) -> collections.UserDict[Scannable | str, float]:
        return self.compute_fit(self._cen, counter, axis)

    def _cen(self, counter, axis: Scannable | str | None):
        return scan_math.cen(*self._get_x_y_data(counter, axis))[0]

    def find_position(
        self,
        func: _NumericFitFunction,
        counter: _countable | None,
        axis: Scannable | str | None = None,
    ) -> collections.UserDict[Scannable | str, float]:
        """Evaluate user supplied scan math function"""

        def _find_custom(counter, axis):
            return func(*self._get_x_y_data(counter, axis))

        return self.compute_fit(_find_custom, counter, axis)

    def compute_fit(
        self,
        func: _CounterAxisFitFunction,
        counter,
        axis: Scannable | str | None = None,
    ) -> collections.UserDict[Scannable | str, float]:
        """
        Returns the expected positions for each axis to fit the counter.

        Arguments:
            func: Fit function returning the axis position
            counter: Counter to fit
            axis: A specific axis to fit, else every axis will be fitted
        """
        motors = self._scan._get_data_axes()
        axes_names = [a.name for a in motors]
        res: collections.UserDict[Scannable | str, float] = _MotionDict()

        if isinstance(axis, str):
            # Special fit on the time
            res[axis] = func(counter=counter, axis=axis)
        elif axis is not None:

            def get_selected_axis(
                motors: list[Axis], axis: Scannable | str
            ) -> Scannable:
                if isinstance(axis, str):
                    axes = [m for m in motors if m.name == axis]
                    if len(axes) == 0:
                        raise ValueError(
                            f"Axis {axis} is not part of the scan '{self._scan.name}'"
                        )
                    return axes[0]
                else:
                    assert axis.name in axes_names
                    return axis

            selected = get_selected_axis(motors, axis)
            res[selected] = func(counter=counter, axis=selected)
        else:
            if len(axes_names) == 1 and axes_names[0] in self._TIME_AXES:
                res[axes_names[0]] = func(counter=counter, axis=axes_names[0])
            elif self._scan.scan_info.get("type") in ["loopscan", "timescan"]:
                # allow "timer axis" for timescan
                time_axis = self._TIME_AXES[0]
                res[time_axis] = func(counter=counter, axis=time_axis)
            else:
                if len(motors) == 0:
                    raise RuntimeError("No axis found in this scan.")
                for mot in motors:
                    res[mot] = func(counter=counter, axis=mot)

        return res

    def goto_fit(self, goto: collections.UserDict[Scannable | str, float], move=None):
        """
        Goto a fitted position.

        If a problem happens during the motion, this tries to move them back
        to the original position.

        Arguments:
            goto: A mapping of axis and fitted position. By specification, axis
                  as strings are time base axis
            move: Callable to execute the motion, usually `_move` or `_umove`.

        Raises:
            RuntimeError: If some axis at time based, or if position value is
                          not expected.
        """
        bad_mot = set([mot for mot, pos in goto.items() if not numpy.isfinite(pos)])

        def axis_name(m: Scannable | str):
            if isinstance(m, str):
                return m
            return m.name

        if len(bad_mot) > 0:
            motors = ", ".join([axis_name(mot) for mot, pos in goto.items()])

            def format_pos(mot: Scannable | str, pos) -> str:
                if mot in bad_mot:
                    return f"{pos} (bad)"
                return f"{pos}"

            pos = [format_pos(m, p) for m, p in goto.items()]
            raise RuntimeError(f"Motor(s) move aborted. Request: {motors} -> {pos}")

        for key in goto.keys():
            if key in self._TIME_AXES:
                RuntimeError("Cannot move. Time travel forbidden.")
            if isinstance(key, str):
                RuntimeError(
                    f"Cannot move. Axis as string are not supposed to be there. Found '{key}'."
                )

        with error_cleanup(
            *goto.keys(), restore_list=(cleanup_axis.POS,), verbose=True
        ):
            if move is not None:
                move(goto, relative=False)
            else:
                group = Group(*goto.keys())
                group.move(goto, relative=False)

    def goto_peak(self, counter, axis=None):
        self.goto_fit(self.peak(counter, axis))

    def goto_min(self, counter, axis=None):
        self.goto_fit(self.trough(counter, axis))

    def goto_com(self, counter, axis=None):
        self.goto_fit(self.com(counter, axis))

    def goto_cen(self, counter, axis=None):
        self.goto_fit(self.cen(counter, axis))

    def goto_custom(
        self,
        func: _NumericFitFunction,
        counter: _countable,
        axis=None,
    ):
        """goto for custom user supplied scan math function"""
        self.goto_fit(self.find_position(func, counter, axis))


@typecheck
def get_counter(counter_name: str):
    """
    Gets a counter instance from a counter name
    """
    for counter in global_map.get_counters_iter():
        if counter.fullname == counter_name:
            return counter
    raise RuntimeError(f"Can't find the counter '{counter_name}'")


def get_selected_counter_name(counter=None) -> str:
    """
    Returns the name of the counter selected.

    That's one of the counters actually selected by `plotselect`. It does not
    mean the counter is actually displayed by Flint.

    Returns ONLY ONE counter.

    Raises RuntimeError if more than one counter is selected.

    Used to determine which counter to use for cen pic curs functions.
    """

    if not current_session.scans:
        raise RuntimeError("Scans list is empty!")
    scan_counter_names = set(current_session.scans[-1].streams.keys())

    scan_display = ScanDisplay()
    selected_counter_names = scan_display.displayed_channels
    alignment_counts = scan_counter_names.intersection(selected_counter_names)

    if not alignment_counts:
        # fall-back plan ... check if there is only one counter in the scan
        alignment_counts2 = {
            c
            for c in scan_counter_names
            if (":elapsed_time" not in c and ":epoch" not in c and "axis:" not in c)
        }
        if len(alignment_counts2) == 1:
            print(f"using {next(iter(alignment_counts2))} for calculation")
            alignment_counts = alignment_counts2
        else:
            raise RuntimeError(
                "No counter selected...\n"
                "Hints: Use flint or plotselect to define which counter to use for alignment"
            )
    elif len(alignment_counts) > 1:
        if counter is None:
            raise RuntimeError(
                "There is actually several counter selected (%s).\n"
                "Only one should be selected.\n"
                "Hints: Use flint or plotselect to define which counter to use for alignment"
                % alignment_counts
            )
        if counter.name in alignment_counts:
            return counter.name
        else:
            raise RuntimeError(
                f"Counter {counter.name} is not part of the last scan.\n"
            )
    counter_name = alignment_counts.pop()

    # Display warning on discrepancy with Flint
    flint = get_flint(mandatory=False, creation_allowed=False)
    if flint is not None:
        flint_selected_names = None
        try:
            plot = flint.get_live_scan("default-curve")
            if plot is not None:
                flint_selected_names = plot.displayed_channels
        except Exception:
            pass
        else:
            if flint_selected_names is None or counter_name not in flint_selected_names:
                log_warning(
                    flint,
                    "The used counter name '%s' is not actually displayed in Flint",
                    counter_name,
                )
            elif counter_name in flint_selected_names and len(flint_selected_names) > 1:
                log_warning(
                    flint,
                    "The used counter name '%s' is not the only one displayed in Flint",
                    counter_name,
                )

    return counter_name


def last_scan_motors():
    """
    Return a list of motor used in the last scan.

    It includes direct motors (the one explicitly requested in the scan) and
    indirect motors used to compute the position of pseudo motors.
    """
    if not len(current_session.scans):
        raise RuntimeError("No scan available.")
    scan = current_session.scans[-1]

    return scan._get_data_axes(include_calc_reals=True)


def _get_default_scan_and_counter(
    scan: Scan | None = None,
    counter: typing.Any | None = None,
) -> tuple[Scan, typing.Any]:
    if scan is None:
        scan = current_session.scans[-1]
    if counter is None:
        counter = get_counter(get_selected_counter_name())
    return scan, counter


def _scan_calc(
    func,
    counter: Optional[_countable] = None,
    axis: Union[Scannable, str, None] = None,
    scan: Optional[Scan] = None,
    marker: bool = True,
    goto: bool = False,
    move=None,
):
    scan, counter = _get_default_scan_and_counter(scan, counter)
    fits = ScanFits(scan)
    if callable(func):
        res = fits.find_position(func, counter, axis=axis)
        func = func.__name__  # for label managment
    else:
        res = getattr(fits, func)(counter, axis=axis)

    if marker:
        clear_markers()
        for ax, value in res.items():
            display_motor(
                ax,
                scan=scan,
                position=value,
                label=func + "\n" + str(value),
                marker_id=func,
            )

            if isinstance(ax, str):
                continue

            # display current position if in scan range
            scan_dat = scan.streams[ax][:]
            if (
                not goto
                and ax.position < numpy.max(scan_dat)
                and ax.position > numpy.min(scan_dat)
            ):
                display_motor(
                    ax,
                    scan=scan,
                    position=ax.position,
                    label="\n\ncurrent\n" + str(ax.position),
                    marker_id="current",
                )
    if goto:
        try:
            fits.goto_fit(res, move=move)
        finally:
            if marker:
                for ax, value in res.items():
                    if isinstance(ax, Axis):
                        display_motor(
                            ax,
                            scan=scan,
                            position=ax.position,
                            label="\n\ncurrent\n" + str(ax.position),
                            marker_id="current",
                        )
    if len(res) == 1:
        return next(iter(res.values()))
    else:
        return res


@typecheck
@shorten_signature(hidden_kwargs=[])
def fwhm(
    counter: Optional[_countable] = None,
    axis: Union[Scannable, str, None] = None,
    scan: Optional[Scan] = None,
):
    """
    Return Full Width at Half of the Maximum of previous scan according to <counter>.
    If <counter> is not specified, use selected counter.

    Example: f = fwhm()
    """
    return _scan_calc("fwhm", counter=counter, axis=axis, scan=scan, marker=False)


@typecheck
@shorten_signature(hidden_kwargs=[])
def cen(
    counter: Optional[_countable] = None,
    axis: Union[Scannable, str, None] = None,
    scan: Optional[Scan] = None,
):
    """
    Return the motor position corresponding to the center of the fwhm of the last scan.
    If <counter> is not specified, use selected counter.

    Example: cen(diode3)
    """
    return _scan_calc("cen", counter=counter, axis=axis, scan=scan)


@typecheck
def find_position(
    func: Callable[[Any, Any], float],
    counter: Optional[_countable] = None,
    axis: Optional[Scannable] = None,
    scan: Optional[Scan] = None,
):
    return _scan_calc(func, counter=counter, axis=axis, scan=scan)


@typecheck
@shorten_signature(hidden_kwargs=[])
def goto_cen(
    counter: Optional[_countable] = None,
    axis: Optional[Scannable] = None,
    scan: Optional[Scan] = None,
    move: Optional[Callable] = None,
):
    """
    Return the motor position corresponding to the center of the fwhm of the last scan.
    Move scanned motor to this value.
    If <counter> is not specified, use selected counter.

    Example: goto_cen()

    Arguments:
        move: Standard move command to be used
    """
    return _scan_calc(
        "cen", counter=counter, axis=axis, scan=scan, goto=True, move=move
    )


@typecheck
@shorten_signature(hidden_kwargs=[])
def com(
    counter: Optional[_countable] = None,
    axis: Union[Scannable, str, None] = None,
    scan: Optional[Scan] = None,
):
    """
    Return center of mass of last scan according to <counter>.
    If <counter> is not specified, use selected counter.

    Example: scan_com = com(diode2)
    """
    return _scan_calc("com", counter=counter, axis=axis, scan=scan)


@typecheck
@shorten_signature(hidden_kwargs=[])
def goto_com(
    counter: Optional[_countable] = None,
    axis: Optional[Scannable] = None,
    scan: Optional[Scan] = None,
    move: Optional[Callable] = None,
):
    """
    Return center of mass of last scan according to <counter>.
    Move scanned motor to this value.
    If <counter> is not specified, use selected counter.

    Example: goto_com(diode2)

    Arguments:
        move: Standard move command to be used
    """
    return _scan_calc(
        "com", counter=counter, axis=axis, scan=scan, goto=True, move=move
    )


@typecheck
@shorten_signature(hidden_kwargs=[])
def peak(
    counter: Optional[_countable] = None,
    axis: Union[Scannable, str, None] = None,
    scan: Optional[Scan] = None,
):
    """
    Return position of scanned motor at maximum of <counter> of last scan.
    If <counter> is not specified, use selected counter.

    Example: max_of_scan = peak()
    """
    return _scan_calc("peak", counter=counter, axis=axis, scan=scan)


@typecheck
@shorten_signature(hidden_kwargs=[])
def goto_peak(
    counter: Optional[_countable] = None,
    axis: Optional[Scannable] = None,
    scan: Optional[Scan] = None,
    move: Optional[Callable] = None,
):
    """
    Return position of scanned motor at maximum of <counter> of last scan.
    Move scanned motor to this value.
    If <counter> is not specified, use selected counter.

    Example: goto_peak()

    Arguments:
        move: Standard move command to be used
    """
    return _scan_calc(
        "peak", counter=counter, axis=axis, scan=scan, goto=True, move=move
    )


@typecheck
@shorten_signature(hidden_kwargs=[])
def trough(
    counter: Optional[_countable] = None,
    axis: Union[Scannable, str, None] = None,
    scan: Optional[Scan] = None,
):
    """
    Return position of scanned motor at minimum of <counter> of last scan.
    If <counter> is not specified, use selected counter.

    Example: min_of_scan = min()

    Arguments:
        move: Standard move command to be used
    """
    return _scan_calc("trough", counter=counter, axis=axis, scan=scan)


@typecheck
@shorten_signature(hidden_kwargs=[])
def goto_min(
    counter: Optional[_countable] = None,
    axis: Optional[Scannable] = None,
    scan: Optional[Scan] = None,
    move: Optional[Callable] = None,
):
    """
    Return position of scanned motor at minimum of <counter> of last scan.
    Move scanned motor to this value.
    If <counter> is not specified, use selected counter.

    Example: goto_min()
    """
    return _scan_calc(
        "trough", counter=counter, axis=axis, scan=scan, goto=True, move=move
    )


@typecheck
def goto_custom(
    func: Callable[[Any, Any], float],
    counter: Optional[_countable] = None,
    axis: Optional[Scannable] = None,
    scan: Optional[Scan] = None,
    move: Optional[Callable] = None,
):
    return _scan_calc(func, counter=counter, axis=axis, scan=scan, goto=True, move=move)


def where():
    """
    Draw a vertical line on the plot at current position of scanned motor.

    Example: where()
    """
    for axis in last_scan_motors():
        display_motor(
            axis, marker_id="current", label="\n\ncurrent\n" + str(axis.position)
        )


def clear_markers():
    for axis in last_scan_motors():
        display_motor(axis, marker_id="cen", position=numpy.nan)
        display_motor(axis, marker_id="peak", position=numpy.nan)
        display_motor(axis, marker_id="com", position=numpy.nan)
        display_motor(axis, marker_id="current", position=numpy.nan)


def goto_click(scatter=False, curve=False, move=None):
    """Move the motor displayed by Flint at the location clicked by the user.

    In the case of a `a2scan` (or any `anscan`) every motors of the scan will be
    moved. For that the selected location is first converted into an index,
    and then this index is converted into each motor positions.

    It supports both curves and scatters, based on the previous scan done by BLISS.

    - For a curve, the x-axis have to display a BLISS motor
    - For a scatter, both x and y axes have to be a BLISS motor

    If both `scatter` and `curve` are false (the default) the last scan is used
    to decide which plot have to be used.

    Arguments:
        scatter: If true, use the default scatter plot
        curve: If true, use the default scatter plot
        move: Standard move command to be used

    Raises:
        RuntimeError: If flint was not open or displayed plot was not properly setup.
    """
    f = get_flint(creation_allowed=False, mandatory=False)
    if f is None:
        raise RuntimeError("Flint was not started")

    if not scatter and not curve:
        session = current_session
        scans = session.scans
        if not scans:
            raise RuntimeError("No scan available; Need to do a scan first!")
        scan = scans[-1]

        scatter_plot = False
        plots = scan.scan_info.get("plots", [])
        if isinstance(plots, list):
            for plot_info in plots:
                kind = plot_info.get("kind")
                if kind == "scatter-plot":
                    scatter_plot = True
                    break
    elif scatter:
        scatter_plot = True
        scan = None
    else:
        scatter_plot = False
        scan = None

    def positions_from_scatter() -> dict[Axis, float]:
        scatter = f.get_live_plot("default-scatter")
        position = scatter.select_points(1)

        axis_1_name = scatter.xaxis_channel_name
        axis_2_name = scatter.yaxis_channel_name
        if axis_1_name is None or axis_2_name is None:
            raise RuntimeError("One of scatter axis is not defined")
        if not axis_1_name.startswith("axis:") or not axis_2_name.startswith("axis:"):
            raise RuntimeError("One of scatter axis is not a motor")
        axis_1_name = axis_1_name.split(":", 1)[-1]
        axis_2_name = axis_2_name.split(":", 1)[-1]
        axis1 = session.env_dict[axis_1_name]
        axis2 = session.env_dict[axis_2_name]

        axis_1_pos, axis_2_pos = position[0]
        return {axis1: axis_1_pos, axis2: axis_2_pos}

    def positions_from_curve() -> dict[Axis, float]:
        curve = f.get_live_plot("default-curve")
        position = curve.select_points(1)
        axis_pos = position[0][0]

        axis_name = curve.xaxis_channel_name
        if axis_name is None:
            raise RuntimeError("One of scatter axis is not defined")
        if not axis_name.startswith("axis:"):
            raise RuntimeError("Can't find an axis on plot")
        axis_name = axis_name.split(":", 1)[-1]

        if scan is None:
            axis = session.env_dict[axis_name]
            return {axis: axis_pos}

        if len(scan._get_data_axes()) <= 1:
            axis = session.env_dict[axis_name]
            return {axis: axis_pos}

        # That's an anscan
        goto: dict[Axis, float] = {}
        ref_positions = scan.streams[f"axis:{axis_name}"][:]
        for axis in scan._get_data_axes():
            target_positions = scan.streams[axis.fullname][:]
            pos = numpy.interp(
                axis_pos,
                ref_positions,
                target_positions,
            )
            goto[axis] = float(pos)
        return goto

    if scatter_plot:
        goto = positions_from_scatter()
    else:
        goto = positions_from_curve()

    with error_cleanup(*goto.keys(), restore_list=(cleanup_axis.POS,), verbose=True):
        if move is not None:
            move(goto, relative=False)
        else:
            group = Group(*goto.keys())
            group.move(goto, relative=False)

    for axis in goto.keys():
        display_motor(
            axis, marker_id="current", label="\n\ncurrent\n" + str(axis.position)
        )
