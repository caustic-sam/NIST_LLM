# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import typing
import typing_extensions


ChannelRef = str
"""Identify a channel from the `scan_info["channels"]` field"""


class ScatterItemDict(typing_extensions.TypedDict):
    """Scatter item description"""

    """Fixed kind for now"""
    kind: typing.Literal["scatter"]

    """Channel for the x-axis"""
    x: typing_extensions.NotRequired[ChannelRef]

    """Channel for the y-axis"""
    y: typing_extensions.NotRequired[ChannelRef]

    """Channel for the value"""
    value: typing_extensions.NotRequired[ChannelRef]


class ScatterPlotDict(typing_extensions.TypedDict):
    """Scatter plot description"""

    """Fixed kind for now"""
    kind: typing.Literal["scatter-plot"]

    """Name of this plot"""
    name: typing_extensions.NotRequired[str]

    """List of the items"""
    items: list[ScatterItemDict]


class CurveItemDict(typing_extensions.TypedDict):
    """Curve item description"""

    """Fixed kind for now"""
    kind: typing.Literal["curve"]

    """Channel for the x-axis"""
    x: typing_extensions.NotRequired[ChannelRef]

    """Channel for the y-axis"""
    y: typing_extensions.NotRequired[ChannelRef]

    """Location of the y-axis for this item"""
    y_axis: typing_extensions.NotRequired[typing.Literal["left", "right"]]


class CurvePlotDict(typing_extensions.TypedDict):
    """Curve plot description"""

    """Fixed kind for now"""
    kind: typing.Literal["curve-plot"]

    """Name of this plot"""
    name: typing_extensions.NotRequired[str]

    """List of the items"""
    items: list[CurveItemDict]


class TablePlotDict(typing_extensions.TypedDict):
    """Table plot description"""

    """Fixed kind for now"""
    kind: typing.Literal["table-plot"]

    """Name of this plot"""
    name: typing_extensions.NotRequired[str]


class OneDimItemDict(typing_extensions.TypedDict):
    """One dim item description"""

    """Fixed kind for now"""
    kind: typing.Literal["curve"]

    """Channel for the value"""
    y: typing_extensions.NotRequired[ChannelRef]


class OneDimPlotDict(typing_extensions.TypedDict):
    """One dim plot description"""

    """Fixed kind for now"""
    kind: typing.Literal["1d-plot"]

    """Name of this plot"""
    name: typing_extensions.NotRequired[str]

    """Channel for the x-axis"""
    x: typing_extensions.NotRequired[ChannelRef]

    """List of the items"""
    items: list[OneDimItemDict]


PlotDict = ScatterPlotDict | CurvePlotDict | TablePlotDict | OneDimPlotDict


class DisplayExtraDict(typing_extensions.TypedDict):
    """Some more stuffs used by Flint"""

    """List of channels which can be selected by the user before a scan"""
    displayed_channels: typing_extensions.NotRequired[list[ChannelRef]]

    """List of channels which was selected by the user at any time in past"""
    plotselect: typing_extensions.NotRequired[list[ChannelRef]]

    """Time in past when the plotselect list was setup.

    It's a Unix time in second."""
    plotselect_time: typing_extensions.NotRequired[float]
