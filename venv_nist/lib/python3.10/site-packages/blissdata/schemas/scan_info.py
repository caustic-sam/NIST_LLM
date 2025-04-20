# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import typing
import typing_extensions
import numpy
from .scan_info_display import PlotDict, DisplayExtraDict


class ChainDict(typing_extensions.TypedDict):
    """Description of the acquisition chain used by a scan"""

    """Key of the very first master"""
    top_master: str

    """Deprecated. List of the whole devices involved by the chain

    The first one is the top master of the chain (see `top_master` above).
    """
    devices: list[str]

    """Deprecated. List of channel name exposing scalar following the time.

    Use the `dim` field from the `channels` entry.
    """
    scalars: list[str]

    """Deprecated. List of channels for exposing 1d data following the time.

    Use the `dim` field from the `channels` entry.
    """
    spectra: list[str]

    """Deprecated. List of channels for exposing 2d data following the time.

    Use the `dim` field from the `channels` entry.
    """
    images: list[str]

    """Deprecated. List of channels from master device per types (scalars, spectra, images)"""
    master: dict[str, list[str]]


class ChannelDict(typing_extensions.TypedDict):
    """Channel description from a scan"""

    """Expected displayed name"""
    display_name: typing_extensions.NotRequired[str]

    """SI unit used by the data"""
    unit: typing_extensions.NotRequired[str]

    """Dimensionality of the data
    (0: scalar, 1: 1D data, 2: 2D data)
    """
    dim: int

    """Prefered number of decimals to display (2 means a template like `0.00`)"""
    decimals: typing_extensions.NotRequired[int]

    """Start position of the axis"""
    start: typing_extensions.NotRequired[float]

    """Stop position of the axis"""
    stop: typing_extensions.NotRequired[float]

    """Minimal value the channel can have"""
    min: typing_extensions.NotRequired[float]

    """Maximal value the channel can have"""
    max: typing_extensions.NotRequired[float]

    """Amount of total points which will be transmitted by this channel"""
    points: typing_extensions.NotRequired[int]

    """Amount of points for the axis (see scatter below)
    """
    axis_points: typing_extensions.NotRequired[int]

    """Number of approximate points expected in the axis
    when this number of points is not regular
    """
    axis_points_hint: typing_extensions.NotRequired[int]

    """Index of the axis in the scatter. 0 is the fastest.
    """
    axis_id: typing_extensions.NotRequired[int]

    """Kind of axis. It is used to speed up solid rendering in
    GUI. Can be one of:

    - `forth`: Move from start to stop always
    - `backnforth`: Move from start to stop to start
    - `step`: The motor position is discrete. The value can be used\
      to group data together.
    """
    axis_kind: typing_extensions.NotRequired[
        typing.Literal["forth", "backnforth", "step"]
    ]

    """Specify a group for the channel. All the channels from the
    same group are supposed to contain the same amount of item at
    the end of the scan. It also can be used as a hint for
    interactive user selection.
    """
    group: typing_extensions.NotRequired[str]


class DeviceDict(typing_extensions.TypedDict):
    """Device description from a scan"""

    """Name of the device

    It is a human readable name of the device.

    In BLISS some devices can have the same name, that is why
    this name is needed, and not the same as the device key.
    """
    name: str

    """List of channels exposed by this device, if some."""
    channels: typing_extensions.NotRequired[list[str]]

    """List of sub devices triggered by this device, if some."""
    triggered_devices: typing_extensions.NotRequired[list[str]]

    """
    One of `lima`, `lima2`, `mca`, `mosca` or nothing. Other values could be
    used but are not yet normalized.

    This field allow to have implicit expectation on information stored for
    the channels, or the related devices.
    """
    type: typing_extensions.NotRequired[str]

    """If defined, 1D channels exposed by this device should be displayed
    using this channel as x-axis"""
    xaxis_channel: typing_extensions.NotRequired[str]

    """If defined, 1D channels exposed by this device should be displayed
    using this array as x-axis"""
    xaxis_array: typing_extensions.NotRequired[numpy.ndarray]

    """Label for the x-axis if `xaxis_array is used"""
    xaxis_array_label: typing_extensions.NotRequired[str]

    """Unit for the x-axis if `xaxis_array` is used"""
    xaxis_array_unit: typing_extensions.NotRequired[str]

    """Metadata exposed by the controllers implementing.

    It is the result of the controller method `scan_metadata`.

    This field is free, an depend on the controller implementation.
    """
    metadata: typing_extensions.NotRequired[dict]


class SequenceDict(typing_extensions.TypedDict):
    """Sequence description"""

    """Number of expected subscans. This can be used to provide a progress bar
    """
    scan_count: typing_extensions.NotRequired[int]


class ScanInfoDict(typing_extensions.TypedDict):
    #
    # Keys reachable after the preparation
    #

    """
    Root information of the chain.
    """
    acquisition_chain: dict[str, ChainDict]

    """
    Information stored per channel names.
    """
    channels: dict[str, ChannelDict]

    """
    Information stored per device names.
    """
    devices: dict[str, DeviceDict]

    """Optional extra information for scan sequence"""
    sequence_info: typing_extensions.NotRequired[SequenceDict]

    """If this scan is part of a sequence, this field reference
    the redis scan key of the parent scan"""
    group: typing_extensions.NotRequired[str]

    """Datetime of the start of the scan as ISO 8601"""
    start_time: str

    """Index of the scan.

    It is not designed to be unique.

    Actually, in BLISS, it stores a number starting at 1 and incremented for every
    new scan. This number is independant for each datasets. It is used to retrieve
    the scan location inside the HDF5.
    """
    scan_nb: int

    """Index of the scan in its parent sequence.

    It can be used when scans can be retied.

    - `0` means the first scan
    """
    index_in_sequence: typing_extensions.NotRequired[int]

    """Number of retry of this scan.

    - `1` means the first retry
    - `0` means not yet retried, in this case, better not to store this key
    """
    retry_nb: typing_extensions.NotRequired[int]

    """True if the scan is a sequence of scans"""
    is_scan_sequence: typing_extensions.NotRequired[typing.Literal[True]]

    """Informative kind for few scans.

    But it is only descriptive.

    Known values are

    - `ct`
    - `timescan`
    - `loopscan`
    - `lookupscan`
    - `pointscan`
    - `ascan`
    - `a2scan`
    - `a3scan`
    - `a4scan`
    - `anscan`
    - `dscan`
    - `d2scan`
    - `d3scan`
    - `d4scan`
    - `dnscan`
    - `amesh`
    - `dmesh`
    """
    type: str

    """Human readable title.

    This field can be set by the user, but by default it is feed with a
    representation of the command line (with absolute position).

    For example `ascan sx 0.0 1.0 10 0.5`
    """
    title: str

    """Nb points of a default BLISS scan.

    - `0` means an infinite scan
    - `1` means a single count

    NOTE: This does not fit multi chains
    """
    npoints: typing_extensions.NotRequired[int]

    """Exposure time of a default BLISS scan

    NOTE: This does not fit multi chains
    """
    count_time: typing_extensions.NotRequired[float]

    """Sleep time of a default BLISS scan

    NOTE: This does not fit multi chains
    """
    sleep_time: typing_extensions.NotRequired[float]

    """Stabilisation time after each motor move of a default BLISS scan.

    NOTE: This does not fit multi chains
    """
    stab_time: typing_extensions.NotRequired[float]

    """Kind of data policy used.

    At the ESRF, it's `ESRF`
    """
    data_policy: str

    """Kind of writer used.

    At the ESRF, it's `nexus`
    """
    data_writer: str

    """Extra configuration for the writer

    Only required if the scan is saved
    """
    writer_options: typing_extensions.NotRequired[dict]

    """Name of the publisher

    At the ESRF it's `bliss`
    """
    publisher: str

    """Version of the publisher

    At the ESRF it's the version of bliss
    """
    publisher_version: str

    """Is the scan data saved?
    """
    save: bool

    """
    Name of the BLISS session, for example `demo_session`
    """
    session_name: str

    """Name of the user of the session.

    At the ESRF it is usually `opd00`, `opid00` or `blissadm`
    """
    user_name: str

    """List of plot description to be displayed"""
    plots: typing_extensions.NotRequired[list[PlotDict]]

    """Some extra display related stuffs used by Flint"""
    display_extra: typing_extensions.NotRequired[DisplayExtraDict]

    #
    # Keys reachable at the end
    #

    """Datetime of the end of the scan as ISO 8601"""
    end_time: typing_extensions.NotRequired[str]

    """Status of the scan termination

    - `SUCCESS`: The scan was terminated normally
    - `FAILURE`: The scan was terminated cause of an error
    - `USER_ABORT`: The scan was aborted by the user
    - `DELETION`: The scan was closed automatically (e.g. bliss segfault)
                  The memory tracker closes them after 24h of inactivity
    """
    end_reason: typing_extensions.NotRequired[
        typing.Literal["SUCCESS", "FAILURE", "USER_ABORT", "DELETION"]
    ]
