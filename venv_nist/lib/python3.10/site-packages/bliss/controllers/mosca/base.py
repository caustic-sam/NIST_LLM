# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import time
import gevent
import enum
import typing
from gevent import event
from itertools import repeat

from bliss import global_map
from bliss.config.settings import OrderedHashObjSetting
from bliss.common.logtools import log_warning, log_debug
from bliss.common.tango import (
    DeviceProxy,
    Database,
    get_tango_device_name_from_url,
    get_tango_host_from_url,
)
from bliss.common.utils import autocomplete_property
from bliss.common.protocols import CounterContainer
from bliss.controllers.counter import CounterController, counter_namespace
from bliss.scanning.chain import AcquisitionMaster
from bliss.scanning.channel import AcquisitionChannel, AcquisitionChannelList
from bliss.common.protocols import HasMetadataForScan
from bliss.controllers.mosca.rois import McaRoi
from bliss.controllers.mosca.counters import SpectrumCounter, StatCounter, ROICounter
from bliss.controllers.mosca.calccounters import (
    CalcROICounter,
    CalcROICounterController,
)
from bliss.controllers.mosca.calccounters import (
    SumROICounter,
    SumCalcROICounterController,
)
import bliss.common.plot as plot_module
from bliss.shell.formatters.table import IncrementalTable

TriggerMode = enum.Enum("TriggerMode", "SOFTWARE SYNC GATE")
PresetMode = enum.Enum("PresetMode", "NONE REALTIME LIVETIME EVENTS TRIGGERS")


class McaCounterController(CounterController, HasMetadataForScan):
    """A MCA CounterController that manages both Spectrum and ROIs counters.
    This object provides the main AcquisitionObject that will drive the acquisition and emit counters data.
    """

    DEVICE_TYPE = "mosca"
    """Normalized device type exposed in the scan info"""

    def __init__(self, name, mca, master_controller=None, register_counters=True):
        super().__init__(name, master_controller, register_counters)
        self._mca = mca

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        return McaAcquisitionMaster(self, ctrl_params=ctrl_params, **acq_params)

    def get_default_chain_parameters(self, scan_params, acq_params):
        scan_points = scan_params.get("npoints", 1)
        trigger_mode = acq_params.get("trigger_mode", TriggerMode.SOFTWARE.name)
        if trigger_mode == TriggerMode.SOFTWARE.name:
            npoints = acq_params.get("npoints", 1)
            start_once = False
            default_wait_frame_id = (
                repeat(npoints, scan_points) if scan_points > 0 else None
            )

        else:
            npoints = acq_params.get("npoints", scan_points)
            start_once = acq_params.get("start_once", True)

            if scan_points == 0:
                npoints = 1
                start_once = False

            if start_once:
                default_wait_frame_id = range(1, npoints + 1)
            else:
                default_wait_frame_id = (
                    repeat(npoints, scan_points) if scan_points > 0 else None
                )

        # Return required parameters
        params = {}
        params["npoints"] = npoints
        params["trigger_mode"] = trigger_mode
        params["start_once"] = start_once
        params["wait_frame_id"] = acq_params.get("wait_frame_id", default_wait_frame_id)
        params["preset_time"] = acq_params.get(
            "preset_time", scan_params.get("count_time", 1.0)
        )
        return params

    def apply_parameters(self, ctrl_params):
        self._mca._check_server_has_restarted()

    def add_roi(self, mca_roi):
        """create counter(s) associated to a ROI"""

        active_channels = self._mca.active_channels.values()

        rchan = mca_roi.channel
        name = mca_roi.name
        start = mca_roi.start
        stop = mca_roi.stop

        if rchan is None:
            for chan in active_channels:
                ROICounter(McaRoi(f"{name}_det{chan:02d}", start, stop, chan), self)

        elif isinstance(rchan, tuple):
            # check if given channels are valid else return
            for chan in rchan:
                if chan not in active_channels:
                    return
            ROICounter(
                McaRoi(f"{name}_sum_{rchan[0]:02d}_{rchan[1]:02d}", start, stop, rchan),
                self,
            )

        elif rchan == -1:
            ROICounter(McaRoi(f"{name}_sum_all", start, stop, rchan), self)

        elif rchan in active_channels:
            ROICounter(McaRoi(f"{name}_det{rchan:02d}", start, stop, rchan), self)

    def remove_roi(self, name):
        roi_names = [
            name for name, cnt in self._counters.items() if isinstance(cnt, ROICounter)
        ]
        for rname in roi_names:
            if rname.startswith(f"{name}_det") or rname.startswith(f"{name}_sum"):
                del self._counters[rname]

    def clear_rois(self):
        names = [
            cnt.name for cnt in self._counters.values() if isinstance(cnt, ROICounter)
        ]
        for cnt_name in names:
            self.remove_roi(cnt_name)

    def dataset_metadata(self) -> dict:
        return {"name": self.name}

    def scan_metadata(self) -> dict:
        return {"type": "mca"}


class McaAcquisitionMaster(AcquisitionMaster):
    def __init__(self, device, ctrl_params=None, **acq_params):

        """
        Acquisition object dedicated to the McaController.

        'ctrl_params' is not used within this class.

        'acq_params' is a dict of acquisition parameters:

            Mandatory keys:
                - "npoints": number of measurements (int)
                - "trigger_mode": trigger mode (str), must be in ['SOFTWARE', 'SYNC', 'GATE']
                - "preset_time": exposure time in seconds (float)

            Optional keys:
                - "start_once": defines if proxy.startAcq() is called only at first iteration (bool, default=False)
                - "wait_frame_id": a list of point numbers for which acquisition waits to allow next iteration (list, default=None)
                - "read_all_triggers": defines if the first point of serie of measurements should be kept or discared,
                                       used only for 'SYNC' trigger mode (bool, default=True)

                XIA specific:
                - "map_pixels_per_buffer": number of pixels per buffer in MAP mode (int, default is auto-tunned)
                - "refresh_rate": a time in seconds (float, default is auto-tunned). It corresponds to the
                                  'proxy.refresh_rate' in MCA mode or to the time between data buffers updates in MAP mode.

        """

        self.acq_params = acq_params
        npoints = self.acq_params["npoints"]
        trigger_mode = self.acq_params["trigger_mode"]
        wait_frame_id = self.acq_params.get("wait_frame_id", None)

        prepare_once = self.acq_params["prepare_once"] = True  # always True

        if trigger_mode == TriggerMode.SOFTWARE.name:
            start_once = self.acq_params[
                "start_once"
            ] = False  # always False in SOFTWARE mode
        else:
            start_once = self.acq_params.setdefault(
                "start_once", False
            )  # or False by default

        self.acq_params.setdefault("read_all_triggers", True)

        # decide this acquisition object's trigger type
        # (see 'trigger_slaves' called by the acqObj of the upper node in acq chain)
        trigger_type = (
            AcquisitionMaster.SOFTWARE
            if trigger_mode == TriggerMode.SOFTWARE.name
            else AcquisitionMaster.HARDWARE
        )

        self.__wait_frame_id_iterator = None
        self.__expected_total_frames_number = None
        self.__force_top_master_one_iter_max = False
        self.__drop_first_point = False

        # =========== ABOUT TRIGGER MODS ======================
        #
        # GENERAL CONCEPTS: SOFTWARE GATE SYNC (valid for all devices handled by Mosca)
        #
        # SOFTWARE:
        #  - device is prepared for a given number of measurements ('npoints')
        #  - device is prepared with a given integration time ('preset_value')
        #  - proxy.startAcq() starts the integration of 'npoints' measurements (like Lima 'INTERNAL' mode)
        #    Note: FalconX and Hamamatsu can only be prepared for ONLY ONE measurement in this mode
        #
        #
        # GATE (FalconX (XIA), Hamamatsu, ):
        #  - device is prepared for a given number of measurements ('npoints')
        #  - proxy.startAcq() put device in a WAIT FOR HW TRIGGER mode
        #  - the gate signal defines the integration time (starts on raise and stop on fall) (POLARITY CAN BE INVERSED)
        #  - 'preset_time' acq param is ignored
        #
        # SYNC (FalconX (XIA), Hamamatsu, OceanOptics):
        #  - device is prepared for a given number of measurements ('npoints')
        #
        #  - FalconX:
        #     - 'preset_time' acq param is ignored
        #     - proxy.startAcq() starts integration (to be verified)
        #     - HW pulse do next measurement (next pixel)
        #
        #  - OceanOptics, Hamamatsu:
        #       - device is prepared with a given integration time ('preset_time' acq param)
        #       - HW pulse starts the integration
        #       - there is a readout time to consider before next pulse

        if not start_once:
            if wait_frame_id is None:
                self.__wait_frame_id_iterator = repeat(npoints)
                self.__force_top_master_one_iter_max = True

            elif wait_frame_id is iter(wait_frame_id):
                self.__wait_frame_id_iterator = wait_frame_id
            else:
                if len(set(wait_frame_id)) != 1:
                    msg = "With start_once=False, elements of 'wait_frame_id' must be all equals to 'npoints'"
                    raise ValueError(msg)

                if wait_frame_id[0] != npoints:
                    msg = "With start_once=False, elements of 'wait_frame_id' must be all equals to 'npoints'"
                    raise ValueError(msg)

                self.__wait_frame_id_iterator = iter(wait_frame_id)
                self.__expected_total_frames_number = len(wait_frame_id)

            self.__drop_frame_id_iterator = repeat(0)

        else:
            if wait_frame_id is None:
                wait_frame_id = [npoints]

            elif wait_frame_id is iter(wait_frame_id):
                # check given wait_frame_id is a finite list (i.e. not a pure iterator)
                msg = "In hardware trigger mode, 'wait_frame_id' must be a finite list"
                raise ValueError(msg)

            elif wait_frame_id[-1] != npoints:
                # check that last value of the given wait_frame_id list corresponds to the last frame number
                raise ValueError(
                    "Last value of 'wait_frame_id' should be the same as 'npoints'"
                )

            self.__wait_frame_id_iterator = iter(wait_frame_id)
            self.__expected_total_frames_number = npoints
            self.__drop_frame_id_iterator = iter(wait_frame_id)

        # =======================================================================================

        AcquisitionMaster.__init__(
            self,
            device,
            name=device.name,
            npoints=npoints,
            trigger_type=trigger_type,
            prepare_once=prepare_once,
            start_once=start_once,
            ctrl_params=ctrl_params,
        )

        self._ready_for_next_iter = event.Event()
        self._ready_for_next_iter.set()
        self.__current_iteration_index = 0
        self.__current_wait_frame_id = 0
        self.__number_of_acquired_frames = 0

        log_debug(self.device._mca, "acq_params: %s", self.acq_params)

    def _init(self, devices):
        self._device, counters = self.init(devices)

        self.channum = self.mca._number_channels
        self.__spectrum_channels = {}
        self.__stat_channels = {}
        self.__roi_channels = AcquisitionChannelList()

        for cnt in counters:
            self.add_counter(cnt)

    def _do_add_counter(self, counter):
        if isinstance(counter, SpectrumCounter):
            controller_fullname, _, _ = counter.fullname.rpartition(":")
            for detnum in self.mca.active_channels.values():
                chan_name = f"{controller_fullname}:spectrum:det{detnum:02d}"
                try:
                    unit = counter.unit
                except AttributeError:
                    unit = None

                acqchan = AcquisitionChannel(
                    chan_name, counter.data_dtype, counter.shape, unit=unit
                )
                self.channels.append(acqchan)
                self._counters[counter].append(acqchan)
                self.__spectrum_channels.setdefault(
                    counter, AcquisitionChannelList()
                ).append(acqchan)

        elif isinstance(counter, StatCounter):
            controller_fullname, _, _ = counter.fullname.rpartition(":")
            for detnum in self.mca.active_channels.values():
                chan_name = f"{controller_fullname}:stat:{counter.name}_det{detnum:02d}"
                try:
                    unit = counter.unit
                except AttributeError:
                    unit = None

                acqchan = AcquisitionChannel(
                    chan_name, counter.data_dtype, counter.shape, unit=unit
                )
                self.channels.append(acqchan)
                self._counters[counter].append(acqchan)
                self.__stat_channels.setdefault(
                    counter, AcquisitionChannelList()
                ).append(acqchan)

        elif isinstance(counter, ROICounter):
            controller_fullname, _, _ = counter.fullname.rpartition(":")
            chan_name = f"{controller_fullname}:roi:{counter.name}"
            try:
                unit = counter.unit
            except AttributeError:
                unit = None

            acqchan = AcquisitionChannel(
                chan_name, counter.data_dtype, counter.shape, unit=unit
            )
            self.channels.append(acqchan)
            self._counters[counter].append(acqchan)
            self.__roi_channels.append(acqchan)

        else:
            super()._do_add_counter(counter)

    @property
    def mca(self):
        return self.device._mca

    @property
    def proxy(self):
        return self.device._mca.hardware

    @property
    def number_of_acquired_frames(self):
        """return the number of currently acquired frames (over the entire acquisition process)"""
        return self.__number_of_acquired_frames

    def __iter__(self):
        while True:
            try:
                self.__current_wait_frame_id = next(self.__wait_frame_id_iterator)
                log_debug(
                    self.device._mca,
                    "iter index: %s, wait frame id: %s",
                    self.__current_iteration_index + 1,
                    self.__current_wait_frame_id,
                )

            except StopIteration as e:
                # handle top master case (when it is possible)
                if (
                    self.parent is None
                    and self.number_of_acquired_frames
                    == self.__expected_total_frames_number
                ):
                    return

                e.args = (
                    self.name,
                    *e.args,
                    f"Unexpected iteration (#{self.__current_iteration_index + 1}), check 'wait_frame_id' has been set properly",
                )
                raise

            yield self
            self.__current_iteration_index += 1
            if self.parent is None and self.__force_top_master_one_iter_max:
                return

    @property
    def spectrum_counters(self):
        return self.__spectrum_channels.keys()

    @property
    def stat_counters(self):
        return self.__stat_channels.keys()

    @property
    def roi_counters(self):
        return (cnt for cnt in self._counters if isinstance(cnt, ROICounter))

    def upload_rois(self):
        # reset proxy rois list
        self.proxy.resetCounters()

        # upload rois list
        self._rois_len = 0
        for cnt in self.roi_counters:
            mca_roi = cnt.roi

            if self.proxy.multichannel:
                if isinstance(mca_roi.channel, tuple):
                    ch1, ch2 = mca_roi.channel
                    idx1 = self.mca._get_alias_index(ch1)
                    idx2 = self.mca._get_alias_index(ch2)
                    roi_values = [
                        mca_roi.name,
                        f"{idx1}-{idx2}",
                        str(mca_roi.start),
                        str(mca_roi.stop),
                    ]
                else:
                    idx = self.mca._get_alias_index(mca_roi.channel)
                    roi_values = [
                        mca_roi.name,
                        str(idx),
                        str(mca_roi.start),
                        str(mca_roi.stop),
                    ]

            elif mca_roi.channel != 0:
                raise ValueError(
                    f"cannot apply roi with channel {mca_roi.channel} on a mono channel device"
                )
            else:
                roi_values = [mca_roi.name, str(mca_roi.start), str(mca_roi.stop)]

            self.proxy.addCounter(roi_values)
            self._rois_len += 1

    def prepare(self):
        if self.__current_iteration_index > 0 and self.prepare_once:
            return

        # perform device specific preparation
        self.mca._prepare_acquisition(self.acq_params)

        if self.acq_params["trigger_mode"] == TriggerMode.SYNC.name:
            if not self.acq_params["read_all_triggers"]:
                self.__drop_first_point = True

        self.specsize = self.proxy.spectrum_size
        self.statnum = len(self.proxy.metadata_labels)
        self.dshape = (self.channum, -1, self.specsize)

        self.upload_rois()

        log_debug(self.device._mca, "proxy.prepareAcq")
        self.proxy.prepareAcq()

    def start(self):
        if self.trigger_type == AcquisitionMaster.SOFTWARE and self.parent:
            # In that case we expect that the parent acqObj will take care of calling
            # 'self.trigger' via its 'trigger_slaves' method
            # (!!! expecting that parent.trigger() method's uses 'trigger_slaves' !!!)
            return

        self.trigger()

    def stop(self):
        log_debug(self.device._mca, "proxy.stopAcq")
        self.proxy.stopAcq()

    def wait_ready(self):
        log_debug(self.device._mca, "ready_for_next_iter WAIT")
        self._ready_for_next_iter.wait()
        log_debug(self.device._mca, "ready_for_next_iter CLEAR")
        self._ready_for_next_iter.clear()

    def trigger(self):
        self.trigger_slaves()

        if self.__current_iteration_index > 0 and self.start_once:
            return

        log_debug(self.device._mca, "proxy.startAcq")
        self.proxy.startAcq()
        self.spawn_reading_task(rawlink_event=self._ready_for_next_iter)

    def emit_data(self, from_index, to_index):
        if from_index >= to_index:
            return

        log_debug(self.device._mca, "emit_data from %s to %s", from_index, to_index)

        spectrum, stats_data, rois_data = self.gather_data(from_index, to_index)

        for cnt in self.spectrum_counters:
            self.__spectrum_channels[cnt].update_from_iterable(
                spectrum[idx, :, :] for idx in self.mca.active_channels
            )

        for cnt in self.stat_counters:
            self.__stat_channels[cnt].update_from_iterable(
                stats_data[idx, :, cnt.label_index] for idx in self.mca.active_channels
            )

        if self.__roi_channels:
            self.__roi_channels.update_from_iterable(rois_data)

    def gather_data(self, from_index, to_index):

        spectrum, stats_data, rois_data = [], [], []

        # === spectrum data
        if self.__spectrum_channels:
            spectrum = self.proxy.getData([from_index, to_index - 1]).reshape(
                self.dshape
            )  # !!! to_index-1 because MOSCA.getData includes the right index

        # === stats data
        if self.__stat_channels:
            stats_data = self.proxy.getMetadataValues(
                [from_index, to_index - 1]
            ).reshape(  # !!! to_index-1 because MOSCA.getMetadataValues includes the right index
                (self.channum, -1, self.statnum)
            )

        # === rois data
        if self.__roi_channels:
            rois_data = self.proxy.getCounterValues([from_index, to_index]).reshape(
                (self._rois_len, -1)
            )

        return spectrum, stats_data, rois_data

    def reading(self):
        """Gather and emit data while acquisition is running.
        Also sets the '_ready_for_next_iter' when it is valid to proceed to the next scan iteration.
        This method is automatically (re)spwaned after each start/trigger call (if not already alive).
        """

        last_curr_pixel = 0
        last_read_pixel = 0
        drop_index = 0
        last_acq_state = None
        last_time = time.perf_counter()
        min_polling_time = 0.01
        max_polling_time = 0.1
        polling_time = min(
            max_polling_time, max(self.acq_params["preset_time"] / 2, min_polling_time)
        )
        log_debug(
            self.device._mca, "ENTER reading loop and set polling time %s", polling_time
        )

        while True:

            # a flag to decide if the status should be emitted
            do_emit_new_status = False

            # === read device status ===
            # state: 0=READY, 1=RUNNING, 2=FAULT
            # read_pixel: available number of pixels (i.e. taken out from device internal buffer)
            # saved_pixel: number of pixels saved by MOSCA server (usually zero in BLISS usage context)
            # curr_pixel: number of pixels acquired by the device (some pixels could still be in the device internal buffer)
            state, read_pixel, saved_pixel, curr_pixel = self.proxy.getAcqStatus()

            # check if acq_state has changed
            if state != last_acq_state:
                last_acq_state = state
                do_emit_new_status = True

            # check if curr_pixel has changed
            delta_curr = curr_pixel - last_curr_pixel
            if delta_curr > 0:
                last_curr_pixel = curr_pixel
                do_emit_new_status = True

            # emit new data
            delta_read = read_pixel - last_read_pixel
            if delta_read > 0:
                self.__number_of_acquired_frames += delta_read
                if self.__drop_first_point:
                    while drop_index >= last_read_pixel and drop_index < read_pixel:
                        self.emit_data(last_read_pixel, drop_index)
                        last_read_pixel = drop_index + 1
                        drop_index = next(self.__drop_frame_id_iterator)

                self.emit_data(last_read_pixel, read_pixel)

                last_read_pixel = read_pixel
                do_emit_new_status = True

            # emit new status
            if do_emit_new_status:
                self.emit_progress_signal(
                    {
                        "curr_pixel": curr_pixel,
                        "read_pixel": read_pixel,
                        "saved_pixel": saved_pixel,
                        "acquired_frames": self.number_of_acquired_frames,
                    }
                )
                log_debug(
                    self.device._mca,
                    "state: %s, curr_pixel: %s, read_pixel: %s, saved_pixel: %s, acquired_frames: %s, drop_index: %s",
                    state,
                    curr_pixel,
                    read_pixel,
                    saved_pixel,
                    self.number_of_acquired_frames,
                    drop_index,
                )
            # raise if detector is in fault
            if last_acq_state == 2:
                raise RuntimeError(
                    f"Detector {self.mca._detector_name} is in Fault state"
                )

            if last_curr_pixel > self.__current_wait_frame_id:
                msg = f"Last acquired frame number ({last_curr_pixel})"
                msg += f" is greater than current wait frame id ({self.__current_wait_frame_id})!\n"
                msg += "It can happen if the detector has received more hardware triggers per scan iteration than expected.\n"
                msg += "Please check that acq param 'wait_frame_id' is compatible with the hardware triggers generation pattern\n"
                msg += "and that hw triggers are not coming too fast between two scan iterations."
                raise RuntimeError(msg)
            elif last_curr_pixel == self.__current_wait_frame_id:
                # check start once instead of prepare once because prepare once is always True
                # start once = True  => HARDWARE trigger => one reading loop will acquire all npoints for the entire scan
                # start once = False => SOFTWARE trigger => one reading loop will acquire npoints per scan iter
                #                    => In that case it is important to wait for state!=1 before allowing next iter
                if self.start_once:
                    if delta_curr > 0:
                        log_debug(
                            self.device._mca,
                            "set ready_for_next_iter from reading (start_once True)",
                        )
                        self._ready_for_next_iter.set()
                elif last_acq_state != 0:
                    # all frames acquired for this iteration but status not ready yet.
                    # So reduce the polling time to re-evaluate the status and exit the loop asap.
                    if polling_time != min_polling_time:
                        polling_time = min_polling_time
                        log_debug(self.device._mca, "set polling time %s", polling_time)

            # exit reading loop when device is ready
            if last_acq_state == 0:
                # ensure all data are gathered before exiting
                if last_read_pixel == self.npoints:
                    break
                log_debug(
                    self.device._mca,
                    "state 0 but gathering not finished %s/%s",
                    last_read_pixel,
                    self.npoints,
                )

            # sleep between [10, 100] milliseconds depending on expo time
            now = time.perf_counter()
            elapsed = now - last_time
            last_time = now
            sleeptime = max(0, polling_time - elapsed)
            gevent.sleep(sleeptime)

        log_debug(self.device._mca, "EXIT reading loop")


class ROIManager:
    def __init__(self, mca_controller):
        self._mca = mca_controller
        self._roi_settings = OrderedHashObjSetting(f"{self._mca.name}_rois_settings")
        self._cached_rois = self._roi_settings.get_all()
        self._create_roi_counters()

    def __info__(self):
        tab = IncrementalTable(
            [["name", "channel", "roi"]], col_sep="", flag="", lmargin=""
        )
        for name, roi_dict in self._cached_rois.items():
            chan = roi_dict["channel"]
            if chan == -1:
                channel = "sum_all"
            elif isinstance(chan, tuple):
                channel = f"sum_{chan[0]}_{chan[1]}"
            elif chan is None:
                channel = "all"
            else:
                channel = chan
            tab.add_line([name, channel, (roi_dict["start"], roi_dict["stop"])])
        tab.resize(8, 20)
        tab.add_separator("-", line_index=1)
        return str(tab)

    # === ROIs management methods
    def _create_roi_counters(self):
        """create roi counters from settings"""
        for roi_dict in self._cached_rois.values():
            self._mca._masterCC.add_roi(McaRoi(**roi_dict))
        self._mca._calcroiCC.update_counters()
        self._mca._sumroiCC.update_counters()

    def _parse_roi_values(self, name, roi_values):
        """return a list of valid mca rois"""
        rvlen = len(roi_values)
        if rvlen < 2 or rvlen > 3:
            raise ValueError(
                "roi values must be a list/tuple of 2 or 3 values: (start_index, stop_index) or (start_index, stop_index, channel_alias)"
            )

        start = int(roi_values[0])
        stop = int(roi_values[1])
        if stop <= start:
            raise ValueError("stop_index must superior to start_index")

        if rvlen == 2:
            chan = None
        elif rvlen == 3:
            chan = self._get_formatted_roi_channel(
                roi_values[2]
            )  # return an int or a tuple (int, int)

        return McaRoi(name, start, stop, chan)

    def _get_formatted_roi_channel(self, chan):
        """format the channel argument provided by a user when defining a roi.
        return channel argument as an int or a tuple (int, int).
        """
        if chan in ["", None]:
            return None

        try:
            return int(chan)
        except ValueError:
            if "-" in chan:
                chan = tuple(map(int, chan.split("-")))
                if len(chan) != 2:
                    raise ValueError(
                        "channels range must be given as 'ch1-ch2' with ch2 > ch1"
                    )
                if chan[0] < 0 or chan[1] < 0:
                    raise ValueError(
                        "channels range must be defined with positive numbers"
                    )
                if chan[1] <= chan[0]:
                    raise ValueError(
                        "channels range must be given as 'ch1-ch2' with ch2 > ch1"
                    )
                return chan
            raise

    def _get_roi(self, name):
        """Get roi from cache (no redis access)"""
        return self._cached_rois[name]

    def _set_roi(self, name, roi_values, updatecc=True):
        """Create roi(s), update cache and store in redis"""
        mca_roi = self._parse_roi_values(name, roi_values)
        if name in self._cached_rois:
            # remove roi before overwriting to avoid duplication in global map
            self.remove(name)
        self._mca._masterCC.add_roi(mca_roi)
        self._roi_settings[name] = mca_roi.to_dict()
        self._cached_rois[name] = mca_roi.to_dict()
        if updatecc:
            self._mca._calcroiCC.update_counters()
            self._mca._sumroiCC.update_counters()

    def _remove_roi(self, name, updatecc=True):
        self._roi_settings.remove(name)
        del self._cached_rois[name]
        self._mca._masterCC.remove_roi(name)
        if updatecc:
            self._mca._calcroiCC.update_counters()
            self._mca._sumroiCC.update_counters()

    def remove(self, *names):
        for name in names:
            self._remove_roi(name, updatecc=False)
        self._mca._calcroiCC.update_counters()
        self._mca._sumroiCC.update_counters()

    # === dict like API

    def set(self, name, roi_values):
        self[name] = roi_values

    def get(self, name, default=None):
        return self._cached_rois.get(name, default)

    def __getitem__(self, name):
        return self._get_roi(name)

    def __setitem__(self, name, roi_values):
        self._set_roi(name, roi_values)

    def __delitem__(self, name):
        self._remove_roi(name)

    def __contains__(self, name):
        return name in self._cached_rois

    def __len__(self):
        return len(self._cached_rois)

    def clear(self):
        names = list(self.keys())
        self.remove(*names)

    def keys(self):
        return self._cached_rois.keys()

    def values(self):
        return self._cached_rois.values()

    def items(self):
        return self._cached_rois.items()

    def update(self, rois):
        for name, roi_values in rois.items():
            self._set_roi(name, roi_values, updatecc=False)
        self._mca._calcroiCC.update_counters()
        self._mca._sumroiCC.update_counters()

    def _load_rois_from_file(self, fpath, separator=" "):
        """
        Load rois from <fpath> file.
        """
        rois_list = []
        with open(fpath) as f:
            for line in f:
                line = line.strip()
                if line:
                    (name, start, stop, channel) = line.split(separator)
                    rois_list.append((name, start, stop, channel))

        # clear all
        names = list(self.keys())
        for name in names:
            self._remove_roi(name, updatecc=False)

        # set all
        for (name, start, stop, channel) in rois_list:
            self._set_roi(name, (start, stop, channel), updatecc=False)

        # update cc
        self._mca._calcroiCC.update_counters()
        self._mca._sumroiCC.update_counters()

    def _load_rois_select_file(self):
        """
        Interactive menu to select a ROI file to load.
        List of ROIs file is taken from ?? directory.
        """

    def load_rois_from_file(self, fpath=None, separator=" "):
        """
        Load ROIs definitions from <fpath> file.
        If <fpath> is not provided, launch a menu to select a file.
        """
        if fpath:
            self._load_rois_from_file(fpath=None, separator=" ")
        else:
            self._load_rois_select_file()


class McaController(CounterContainer):
    """Base class for MCA controllers (as MOSCA client)

    YAML CONFIGURATION EXAMPLE:

      - name: falconx
        class: FalconX
        module: mosca.xia
        plugin: generic

        tango_name: id00/falconx/txo

        roi_correction_formula: roi / (1-deadtime) / iodet

        external_counters:
          iodet: $diode1


    # Valid variables for the 'roi_correction_formula' are:
    #  - 'roi'
    #  - a stat label (ex: 'icr', 'ocr', 'deadtime', ...)
    #  - an external_counter tag as declared below 'external_counters' (ex: 'iodet')

    """

    STATS_MAPPING = {}

    def __init__(self, config):
        self._config = config
        self._name = config["name"]
        self._hw_controller = None
        self._detector_name = None
        self._detector_model = None
        self._number_channels = None
        self._spectrum_size = None
        self._settings = OrderedHashObjSetting(f"{self._name}_ctrl_settings")
        self._masterCC = McaCounterController(self.name, self)
        self._calcroiCC = None
        self._sumroiCC = None

        self.initialize()

    def __info__(self):
        self._check_server_has_restarted()
        txt = f"=== MCA controller: {self.config['tango_name']} ===\n"
        txt += f" detector name:     {self._detector_name}\n"
        txt += f" detector model:    {self._detector_model}\n"
        txt += f" channels number:   {self._number_channels}\n"
        txt += f" spectrum size:     {self._spectrum_size}\n"
        txt += f" trigger mode:      {self.trigger_mode}\n"
        txt += f" preset mode:       {self.preset_mode}\n"
        txt += f" preset value:      {self.hardware.preset_value/1000} s\n"
        return txt

    def _load_settings(self):
        pass

    def _get_hardware_info(self):
        self._detector_name = self.hardware.detector_name
        self._detector_model = self.hardware.detector_model
        self._number_channels = self.hardware.number_channels
        self._spectrum_size = self.hardware.spectrum_size
        self._build_channels_mapping()

    def _create_counters(self):
        for cc in [self._calcroiCC, self._sumroiCC]:
            if cc is not None:
                cc._global_map_unregister()

        # === instantiations order matters!
        self._masterCC._counters.clear()
        SpectrumCounter("spectrum", self._masterCC)
        for label_index, label in enumerate(self.hardware.metadata_labels):
            if label not in ["chnum", "deadtime_correction"]:
                StatCounter(
                    f"{self.STATS_MAPPING.get(label, label)}",
                    label_index,
                    self._masterCC,
                )
        self._calcroiCC = CalcROICounterController(
            f"{self.name}:roi_correction", self, self._config
        )
        self._sumroiCC = SumCalcROICounterController(
            f"{self.name}:roi_sum", self._calcroiCC
        )
        self._rois = ROIManager(self)
        # ==================================

    def _get_default_chain_counter_controller(self):
        return self._masterCC

    def _build_channels_mapping(self):
        """Build mapping between channels aliases and corresponding data indexes"""
        self._chan2index = {}
        self._index2chan = {}
        for idx, chan in enumerate(self.detectors_aliases):
            idx = int(idx)
            chan = int(chan)
            self._chan2index[chan] = idx
            self._index2chan[idx] = chan

    def _get_alias_index(self, channel_alias):
        if channel_alias == -1:
            return channel_alias
        return self._chan2index[channel_alias]

    def _prepare_acquisition(self, acq_params):
        self.hardware.trigger_mode = acq_params["trigger_mode"]
        self.hardware.number_points = acq_params["npoints"]
        self.hardware.preset_value = acq_params["preset_time"] * 1000  # milliseconds

    def _check_server_has_restarted(self):
        device_name = get_tango_device_name_from_url(self._config["tango_name"])
        tango_host = get_tango_host_from_url(self._config["tango_name"])
        server_started_date = (
            Database(tango_host).get_device_info(device_name).started_date
        )
        server_start_timestamp = self._settings.get("server_start_timestamp")
        if server_start_timestamp != server_started_date:
            self._settings["server_start_timestamp"] = server_started_date
            if server_start_timestamp is not None:
                log_warning(self, "re-initializing because server has been restarted")
                self.initialize()
                return True
        return False

    def _update_global_map_calccounters(self, value):
        # enable / disable calc_counters in GlobalMap (and therefore MeasurementGroups)
        for cnt in self._calcroiCC.outputs + self._sumroiCC.outputs:
            if value:
                global_map.register(cnt, parents_list=["counters"])
            else:
                global_map.unregister(cnt)

    def initialize(self):
        self._load_settings()
        self._get_hardware_info()
        self._create_counters()
        self._update_global_map_calccounters(self._calcroiCC.calc_formula)

    def edit_rois(self, acq_time: typing.Optional[float] = None):
        """
        Edit this detector ROIs with Flint.

        When called without arguments, it will use the data from specified detector
        from the last scan/ct as a reference. If `acq_time` is specified,
        it will do a `ct()` with the given count time to acquire a new data.

        .. code-block:: python

            # Flint will be open if it is not yet the case
            mca1.edit_rois(0.1)

            # Flint must already be open
            ct(0.1, mca1)
            mca1.edit_rois()
        """
        # Check that Flint is already there
        flint = plot_module.get_flint()
        plot_proxy = flint.get_live_plot(mca_detector=self.name)

        if acq_time is not None:
            # Open flint before doing the ct
            from bliss.common import scans

            s = scans.ct(acq_time, self.counters.spectrum)
            plot_proxy.wait_end_of_scan(s)

        ranges = plot_proxy.get_data_range()
        if ranges[0] is None:
            raise RuntimeError(
                "edit_rois: Not yet spectrum in Flint. Do 'ct' first or specify an 'acq_time'"
            )

        # Retrieve all the ROIs
        selections = []
        for roi_dict in self._rois._cached_rois.values():
            selections.append(McaRoi(**roi_dict))

        print(f"Waiting for ROI edition to finish on {self.name}...")
        plot_proxy.focus()
        selections = plot_proxy.select_shapes(
            selections,
            kinds=[
                "mosca-range",
            ],
        )

        self._rois.clear()
        for mca_roi in selections:
            self._rois._set_roi(
                mca_roi.name, (mca_roi.start, mca_roi.stop, mca_roi.channel)
            )

        roi_string = ", ".join(sorted([s.name for s in selections]))
        print(f"Applied ROIS {roi_string} to {self.name}")

    @property
    def name(self):
        return self._name

    @property
    def config(self):
        return self._config

    @property
    def detectors_identifiers(self):
        """return active detectors identifiers list [str]"""
        # by default return the list of channels indexes
        return [str(i) for i in range(self._number_channels)]

    @property
    def detectors_aliases(self):
        """return active detectors channels aliases list [int]"""
        # by default return the list of channels indexes
        return range(self._number_channels)

    @property
    def active_channels(self):
        """return active channels as a dict {index: channel_alias}"""
        return self._index2chan

    @property
    def preset_mode(self):
        return self.hardware.preset_mode

    @preset_mode.setter
    def preset_mode(self, value):
        value = str(value).upper()
        if value not in [x.name for x in PresetMode]:
            raise ValueError(f"preset mode should be in {[x.name for x in PresetMode]}")
        self.hardware.preset_mode = value

    @property
    def trigger_mode(self):
        return self.hardware.trigger_mode

    @trigger_mode.setter
    def trigger_mode(self, value):
        value = str(value).upper()
        if value not in [x.name for x in TriggerMode]:
            raise ValueError(
                f"trigger mode should be in {[x.name for x in TriggerMode]}"
            )
        self.hardware.trigger_mode = value

    @autocomplete_property
    def hardware(self):
        if self._hw_controller is None:
            self._hw_controller = DeviceProxy(self._config["tango_name"])
        return self._hw_controller

    @autocomplete_property
    def counters(self):
        all_counters = self._masterCC.counters
        if self.calc_formula:
            all_counters += self._calcroiCC.outputs + self._sumroiCC.outputs
        return all_counters

    @autocomplete_property
    def counter_groups(self):
        dct = {}

        # Spectrum counter
        dct["spectrum"] = counter_namespace(
            [cnt for cnt in self.counters if isinstance(cnt, SpectrumCounter)]
        )
        dct["stat"] = counter_namespace(
            [cnt for cnt in self.counters if isinstance(cnt, StatCounter)]
        )
        dct["roi"] = counter_namespace(
            [cnt for cnt in self.counters if isinstance(cnt, ROICounter)]
        )
        dct["roi_corr"] = counter_namespace(
            [cnt for cnt in self.counters if isinstance(cnt, CalcROICounter)]
        )
        dct["roi_sum"] = counter_namespace(
            [cnt for cnt in self.counters if isinstance(cnt, SumROICounter)]
        )

        # Default grouped
        dct["default"] = counter_namespace(
            list(dct["spectrum"])
            + list(dct["stat"])
            + list(dct["roi"])
            + list(dct["roi_corr"])
            + list(dct["roi_sum"])
        )

        # Return namespace
        return counter_namespace(dct)

    @autocomplete_property
    def rois(self):
        return self._rois

    @property
    def calc_formula(self):
        return self._calcroiCC.calc_formula

    @calc_formula.setter
    def calc_formula(self, value):
        self._calcroiCC.calc_formula = value
        self._update_global_map_calccounters(value)
