# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from __future__ import annotations

import gevent
import numpy
import typing
import contextlib

from bliss.common import event
from bliss.common.utils import get_matching_names
from bliss.common.logtools import log_debug
from prompt_toolkit.formatted_text import FormattedText

if typing.TYPE_CHECKING:
    from bliss.scanning.scan import Scan


class ScanProgress:
    """
    This object can be passed to a Scan object (see 'scan_progress') in order to
    store and access information about the progress of the scan and all
    acquisition objects that emit the 'scan_progress' signal (see
    'AcquisitionObject.emit_progress_signal').

    In addition, an optional argument 'tracked_channels' can be passed in order
    to ask to store the last data emitted by specific channels.

    tracked_channels is a list of names that should match the name of the
    channels involved in the scan.

    Use tracked_channels = 'all' to listen all the 0D channels of a scan.
    """

    USE_TEXTBLOCK: bool = False
    """By default this is set to False for compatibility with third party code.
    """

    def __init__(self, tracked_channels=None):

        self._tracked_channels = (
            tracked_channels if tracked_channels is not None else []
        )

        self._scan = None
        self._scan_info = None
        self._scan_type = None
        self._scan_axes_pos = None
        self._frame_rate = 2
        self._connected = False
        self._data_dict = {}

    def _update_data(self, data_dict):
        self._data_dict.update(data_dict)

    @property
    def data(self):
        return self._data_dict

    @property
    def scan_info(self):
        return self._scan_info

    @property
    def scan_state(self):
        return self._scan.state

    @property
    def scan_type(self):
        return self._scan_type

    @property
    def acq_objects(self):
        return self._scan.acq_chain.nodes_list

    @property
    def acq_channels(self):
        return [ch for acq in self.acq_objects for ch in acq.channels]

    def find_axes(self):
        """find axes involved in the scan and return {axis_channel: owner_acq_obj}"""
        chan2acq = {}
        for acq in self.acq_objects:
            for ch in acq.channels:
                if ch.fullname.startswith("axis"):  # it includes reals of calc_mot
                    chan2acq[ch] = acq
        return chan2acq

    def _update_axes_pos(self):
        """update local data dict with axes positions (from axis.position)"""
        for ch, acq in self._axes_chan2acq.items():
            try:
                pos = acq.device.axes_with_reals[ch.short_name].position
            except AttributeError:
                pos = numpy.nan
            self._update_data({ch.name: pos})

    def on_scan_new(self, scan, scan_info):
        self._scan = scan
        self._scan_info = scan_info
        self._scan_type = scan_info.get("type")
        self._axes_chan2acq = (
            self.find_axes()
        )  # all axes channels in scan chain (pseudos and reals)
        self._scan_axes_pos = {
            axis: axis.position for axis in scan._get_data_axes()
        }  # axes passed to scan cmd only
        self._update_axes_pos()
        self.scan_init_callback()
        self.connect()

    def on_scan_end(self, scan_info):
        self._update_axes_pos()
        self.scan_end_callback()
        self.disconnect()
        self._axes_chan2acq.clear()
        self._scan_axes_pos.clear()

    def new_data_received(self, data=None, signal=None, sender=None):
        """sender is channel"""
        if data is not None:
            try:
                self._update_data({sender.fullname: data[-1]})
            except Exception:
                pass

    def new_position_received(self, data=None, signal=None, sender=None):
        """sender is axis"""
        if data is not None:
            try:
                self._scan_axes_pos[sender] = data
            except Exception:
                pass

    def scan_progress_received(self, event_dict=None, signal=None, sender=None):
        self._update_data({event_dict["name"]: event_dict["data"]})

    def connect(self):
        if self._connected:
            return

        # === connect to the new_data signal of the given tracked channels
        self.__connected_channels = []
        channels = self.acq_channels

        if self._tracked_channels == "all":
            for ch in channels:
                if len(ch.shape) == 0:  # connect 0D chan only
                    event.connect(ch, "new_data", self.new_data_received)
                    log_debug(self, f"connecting to {ch.name} ({ch.fullname})")
                    self.__connected_channels.append(ch)
        else:
            # === always add involved axes to the tracked channels list
            self._tracked_channels.extend(
                [ch.name for ch in self._axes_chan2acq.keys()]
            )

            # === find channel fullnames from the tracked channel list
            name2chan = {ch.fullname: ch for ch in channels}
            matches = get_matching_names(
                self._tracked_channels,
                name2chan.keys(),
                strict_pattern_as_short_name=True,
            )

            # === get tracked channel list without duplicated names
            chnames = set([chname for v in matches.values() for chname in v])

            # === connect to tracked channels
            for chname in chnames:
                ch = name2chan[chname]
                event.connect(ch, "new_data", self.new_data_received)
                log_debug(self, f"connecting to {chname} ({ch.fullname})")
                self.__connected_channels.append(ch)

        # === connect to scan_progress signal (acq_obj)
        for acq in self.acq_objects:
            log_debug(self, f"connecting to {acq} ({acq.name}) scan_progress")
            event.connect(acq, "scan_progress", self.scan_progress_received)

        # === connect to axes position signal
        for axis in self._scan_axes_pos:
            event.connect(axis, "position", self.new_position_received)

        self._connected = True

    def disconnect(self):
        if not self._connected:
            return

        for ch in self.__connected_channels:
            event.disconnect(ch, "new_data", self.new_data_received)
            log_debug(self, f"disconnecting {ch.fullname} from new_data")

        for acq in self.acq_objects:
            event.disconnect(acq, "scan_progress", self.scan_progress_received)
            log_debug(self, f"disconnecting {acq} ({acq.name}) from scan_progress")

        # === disconnect from axes position signal
        for axis in self._scan_axes_pos:
            event.disconnect(axis, "position", self.new_position_received)

        self._connected = False

    def progress_callback(self):
        pass

    def scan_init_callback(self):
        """Call before the the start of the progress bar, when the scan is not yet started.

        It is called from the greenlet executing `scan.run`.
        """

    def scan_new_callback(self):
        """Call during the progress bar, when the scan state is already known.

        It is called from a child of the greenlet executing `scan.run`.
        """

    def scan_end_callback(self):
        """Call at the end of the progress bar

        It is called from the greenlet executing `scan.run`.
        """

    @contextlib.contextmanager
    def exec_context(self, scan: Scan, capture):
        self.on_scan_new(scan, scan.scan_info)
        try:
            if not self.USE_TEXTBLOCK:
                task = gevent.spawn(self.progress_task)
                try:
                    yield
                finally:
                    with capture():
                        if task:
                            if task.ready():
                                if not task.successful():
                                    task.get()
                            else:
                                task.kill()
            else:
                from bliss.shell.standard import text_block
                from bliss.shell.pt import utils

                task = gevent.spawn(self._wait_for_scan_initialization)

                try:
                    if utils.can_use_text_block():
                        with text_block(render=self._render):
                            yield
                    else:
                        yield
                finally:
                    task.kill()
        finally:
            with capture():
                self.on_scan_end(self.scan_info)

    #
    # Implementation without TextBlock
    #

    def _wait_for_scan_initialization(self):
        from bliss.scanning.scan import ScanState

        self._scan.wait_state(ScanState.PREPARING)
        self.scan_new_callback()

    def progress_task(self):
        """Updates the scan progress at the defined frame rate

        This function is not called when `USE_TEXTBLOCK == True`
        """
        self._wait_for_scan_initialization()
        while True:
            try:
                self.progress_callback()
            except Exception as e:
                print(f"progress_task exception: {e}")
                break
            gevent.sleep(1 / self._frame_rate)

    #
    # Implementation with TextBlock
    #

    def build_progress_message(self) -> str | tuple[int, str | FormattedText]:
        """Format the text to be displayed by the progress bar.

        This function have to be implemented to tell .

        Returns:
            str: A single line (for compatibility)
            tuple[int, str | FormattedText]: A tuple with the number of line
                to be displayed and the content to be displayed
        """
        raise NotImplementedError("Have to be implemented")

    def _render(self) -> tuple[int, str | FormattedText]:
        """Called time to time by the text block application to format the text
        to display"""
        data = self.build_progress_message()
        if isinstance(data, str):
            # Compatibility with the older code
            return 1, data
        else:
            return data
