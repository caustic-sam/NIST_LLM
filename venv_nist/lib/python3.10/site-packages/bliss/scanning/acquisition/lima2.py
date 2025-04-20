# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import logging
import gevent
from glom import glom
from gevent import event
from itertools import repeat
from pathlib import Path

from bliss.scanning.chain import AcquisitionMaster, AcquisitionSlave
from bliss.scanning.channel import Lima2AcquisitionChannel
from bliss.common.os_utils import makedirs
from bliss.controllers.lima2.device import Lima2

from lima2.client import State

_logger = logging.getLogger("bliss.scans.lima2")


# Logger decorator
def logger(fn):
    def inner(*args, **kwargs):
        _logger.debug(f"Entering {fn.__name__}")
        to_execute = fn(*args, **kwargs)
        _logger.debug(f"Exiting {fn.__name__}")
        return to_execute

    return inner


class Lima2AcquisitionMaster(AcquisitionMaster):
    """
    AcquisitionMaster object for 2D lima detectors.
    Controls the acquisition of images during a BLISS scanning procedure.
    It takes a dictionary of acquisition parameters 'acq_params' that describes
    when and how images will be acquired:

    acq_params keys:
        'acq_nb_frames'    : the number of frames for which a detector is prepared (0 for an infinite number of frames)
        'acq_expo_time'    : the detector exposure time in seconds
        'acq_trigger_mode' : the triggering mode in ['INTERNAL_TRIGGER', 'INTERNAL_TRIGGER_MULTI', 'EXTERNAL_TRIGGER', 'EXTERNAL_TRIGGER_MULTI', 'EXTERNAL_GATE', 'EXTERNAL_START_STOP']
        'acq_mode'         : the acquisition mode in ['SINGLE', 'CONCATENATION', 'ACCUMULATION']
        'prepare_once'     : False if the detector should be prepared before each scan iteration (prepared for 'acq_nb_frames' each time)
        'start_once'       : False if detector.startAcq() should be called at each scan iteration
        'wait_frame_id'    : (optional) A list of frames IDs for which this object should wait before proceeding to the next scan iteration (it could be an iterator too)

    Note: `wait_frame_id` is the frame number to wait for the next sequence in case the synchronisation is base on data.
    i.e: for a mesh with one fast axes (continous), combine with one slow step motor. if you do 20 images per line,
    the wait_frame_id must be equal to :code:`range(0,TOTAL_IMAGE,IMAGE_PER_LINE)`.
    """

    def __init__(self, device, name, ctrl_params=None, **acq_params):
        # ensure that ctrl-params have been completed
        ctrl_params = self.init_ctrl_params(device, ctrl_params)

        self.acq_params = acq_params

        _logger.debug(f"ctrl_params: {ctrl_params} acq_params: {acq_params}")

        nb_frames = self.acq_params["nb_frames"]

        # # deal with 'ONE_FILE_PER_SCAN' mode
        # if ctrl_params.get("saving_frame_per_file") == -1:
        #     ctrl_params["saving_frame_per_file"] = nb_frames

        trigger_type = (
            AcquisitionMaster.SOFTWARE
            if self.acq_params["trigger_mode"] in ["internal", "software"]
            else AcquisitionMaster.HARDWARE
        )

        AcquisitionMaster.__init__(
            self,
            device,
            name=name,
            npoints=nb_frames,
            trigger_type=trigger_type,
            prepare_once=self.acq_params["prepare_once"],
            start_once=self.acq_params["start_once"],
            ctrl_params=ctrl_params,
        )

        self._dev: Lima2 = self.device._dev  # Note self.device is FrameController
        self._ready_event = event.Event()
        self._ready_event.set()
        self.__sequence_index = 0

        if self.npoints <= 0:
            # 'infinite' acquisition;
            # supposedly, frame by frame: there are N iterations of 1 frame
            self.__frames_per_iteration_iter = repeat(0)
        else:
            frame_number_range = self.acq_params.pop("wait_frame_id", None)
            if frame_number_range is None:
                # npoints represent the number of frames to be taken;
                # there are N iterations of X frames, N*X=npoints
                if acq_params["prepare_once"]:
                    self.__frames_per_iteration_iter = iter(range(self.npoints))
                else:
                    self.__frames_per_iteration_iter = repeat(self.npoints - 1)
            else:
                self.__frames_per_iteration_iter = iter(frame_number_range)
        self._current_wait_frame_id = None

    @staticmethod
    def get_param_validation_schema():
        _logger.debug("In get_param_validation_schema")
        return {}

    @classmethod
    def validate_params(cls, acq_params, ctrl_params=None):
        # TODO deals with validation later (bypass BlissValidator)
        _logger.debug(
            f"In validate_params ctrl_params: {ctrl_params} acq_params: {acq_params}"
        )
        return acq_params

    @property
    def fast_synchro(self):
        # return self._dev._det.synchro_mode == "TRIGGER"
        return False

    @logger
    def __iter__(self):
        while True:
            try:
                self._current_wait_frame_id = next(self.__frames_per_iteration_iter)
            except StopIteration as e:
                e.args = (
                    self.device.name,
                    *e.args,
                    "Synchronisation error, **wait_frame_id** is wrongly set for this scan",
                )
                raise
            yield self
            if self.parent is None:
                # we have to stop iterations ourselves
                return
            self.__sequence_index += 1

    @logger
    def add_counter(self, counter):
        """Called right after the AcquisitionObject creation"""
        if counter in self._counters:
            return

        self.channels.append(
            Lima2AcquisitionChannel(
                f"{self.name}:{counter.name}",
                self._dev._server_urls,
                counter.dtype,
                counter.shape,
                counter.saving_spec,
                counter.file_only,
            )
        )
        _logger.debug(f"Created image channel {self.name}:{counter.name}")

        self._counters[counter].append(self.channels[-1])

    @logger
    @property
    def save_flag(self):
        return bool(self.channels)

    @logger
    def prepare(self):
        # should be moved to scan framework
        if self.__sequence_index > 0 and self.prepare_once:
            return

        if self._dev.state != State.IDLE:
            _logger.error(f"LIMA2 {self.name} is in Fault state")
            RuntimeError(f"LIMA2 {self.name} is in Fault state")

        _logger.debug(f"-> ctrl_params: {self.ctrl_params}")
        _logger.debug(f"-> acq_params: {self.acq_params}")

        # Update parameters before prepare
        for p in [self.ctrl_params["ctrl"], self.ctrl_params["recvs"]]:
            p["acq"]["nb_frames"] = self.acq_params["nb_frames"]
            p["acq"]["expo_time"] = int(self.acq_params["expo_time"] * 1e6)
            p["acq"]["latency_time"] = int(self.acq_params.get("latency_time", 0) * 1e6)
            p["acq"]["trigger_mode"] = self.acq_params["trigger_mode"]
            p["acq"]["nb_frames_per_trigger"] = self.acq_params.get(
                "nb_frames_per_trigger", 1
            )

        # Update channel parameters from saving parameters
        nb_saving = 0
        for channel in self.channels:
            if channel.saving_spec is not None:
                saving_params = glom(self.ctrl_params, channel.saving_spec)
                if not isinstance(saving_params, list):
                    saving_params = [saving_params]

                if self.acq_params["is_saving"] is True:
                    for s in saving_params:
                        if s["enabled"]:
                            nb_saving += 1
                            # Update saving parameters first
                            s.update(
                                {
                                    "base_path": self.acq_params["saving_directory"],
                                    "filename_prefix": self.acq_params["saving_prefix"]
                                    + channel.short_name,
                                    "nx_detector_name": self.name,
                                }
                            )

                            # Update channel parameters
                            channel._lima_info.update(self._get_saving_description(s))
                else:
                    for s in saving_params:
                        s.update({"enabled": False})

        # If saving required, check that at least one saving channel is enabled
        if self.acq_params["is_saving"] is True and nb_saving == 0:
            _logger.error(
                f"LIMA2 {self.name} saving requested but no saving channel enabled"
            )
            raise RuntimeError(
                f"LIMA2 {self.name} saving requested but no saving channel enabled"
            )

        # TODO
        from uuid import uuid1

        uuid = uuid1()
        _logger.debug(f"UUID {uuid}")
        self.acq_uuid = uuid

        self._dev.prepare(
            uuid,
            self.ctrl_params["ctrl"],
            self.ctrl_params["recvs"],
            self.ctrl_params["procs"],
        )

        p = self._dev.current_pipeline

        channels_metadata = p.channels

        # TODO Get channel data from receivers
        channels_metadata.update({"raw_frame": channels_metadata["frame"]})

        # Update channel description with the newly constructed processing
        for channel in self.channels:
            # Update shape and dtype
            metadata = channels_metadata[channel.short_name]
            channel._shape = metadata["shape"]
            channel._dtype = metadata["dtype"]

        # Handle processing failure, stop the scan if it's currently reading
        def on_error(evt):
            # If the processing is the one currently feeded by the acquisition
            if not evt.err and p.uuid == self.acq_uuid and self._reading_task:
                _logger.error(
                    f"LIMA2 {self.name} processing {p.uuid} failed with {p.last_error}"
                )
                # self.stop()
                self._reading_task.kill(
                    RuntimeError(
                        f"LIMA2 {self.name} processing {p.uuid} failed with {p.last_error}"
                    )
                )
                # self._dev._det.error()

        p.register_on_error(on_error)

        _logger.debug(f"state {self._dev.state}")

        self._nb_triggers = 0

    @logger
    def start(self):
        # ! STARTS must be called independently of the trigger_type !
        # if self.trigger_type == AcquisitionMaster.SOFTWARE and self.parent:
        #     # otherwise top master trigger would never be called
        #     return

        if self.__sequence_index > 0 and self.start_once:
            # run start only for the first sequence
            if self.trigger_type == AcquisitionMaster.SOFTWARE and not self.parent:
                # We dont have a top master
                self.trigger()
            return

        self._dev.start()

        self.spawn_reading_task(rawlink_event=self._ready_event)

        _logger.debug(f"state {self._dev.state}")

    @logger
    def stop(self):
        self._dev.stop()
        self._dev._client.clear_previous_pipelines()

    # def trigger_ready(self):
    #     return True

    @logger
    def wait_ready(self):
        _logger.debug("ready event wait START")
        self._ready_event.wait()
        _logger.debug("ready event wait END")
        self._ready_event.clear()
        _logger.debug("ready event CLEAR")

    @logger
    def trigger(self):
        self.trigger_slaves()

        self._nb_triggers += 1

        self._dev.trigger()

    @logger
    def set_device_saving(self, directory, prefix, force_no_saving=False):
        """Called by the writer when preparing the scan"""
        if self.save_flag and not force_no_saving:
            self.acq_params["is_saving"] = True
            self.acq_params["saving_directory"] = directory

            # if path is valid, create directory (if it doesnt exist yet)
            if not Path(directory).exists():
                makedirs(directory, exist_ok=True)

            self.acq_params.setdefault("saving_prefix", prefix)
        else:
            self.acq_params["is_saving"] = False

    def _get_saving_description(self, saving_params):
        """Returns lima_info channel"""
        entry_name = saving_params["nx_entry_name"]
        instrument_name = saving_params["nx_instrument_name"]
        detector_name = saving_params["nx_detector_name"]

        description = {
            # Lima2
            # "compression": saving_params["compression"],
            "file_format": "hdf5",
            "path_template": saving_params["filename_format"],
            "data_path": f"{entry_name}/{instrument_name}/{detector_name}/data",
            "frame_per_file": saving_params["nb_frames_per_file"],
            "frame_per_acquisition": self.npoints,
            "directory": saving_params["base_path"],
            "rank": saving_params["filename_rank"],
            "prefix": saving_params["filename_prefix"],
            "suffix": saving_params["filename_suffix"],
            # "user_detector_name": user_detector_name,
            # "user_instrument_name": user_instrument_name,
            # "lima_version": version["lima"],
        }
        return description

    def _emit_status(self, status, progress_counters):
        progress_keys = ["nb_frames_acquired", "nb_frames_xferred"]
        self.emit_progress_signal(
            {"state": status["state"].name}
            | {k: status[k] for k in progress_keys}
            | {name: counter for name, counter in progress_counters.items()}
        )

        if self.channels and status["nb_frames_xferred"] >= 0:
            # Send to Lima2Client.update()
            payload = {
                # UUID is not serializable, convert to string
                "acq_uuid": str(self.acq_uuid),
                "nb_frames_acquired": status["nb_frames_acquired"],
                "nb_frames_xferred": status["nb_frames_xferred"],
            }

            # TODO each channel should evolve at its own pace
            for ch in self.channels:
                ch.emit(payload)

    @logger
    def reading(self):
        """Gather and emit lima status while camera is running (acq_state and last image info).
        Also sets the '_ready_event' when it is valid to proceed to the next scan iteration.
        For each 'prepare', camera is configured for the acquisition of 'acq_nb_frames'
        and this method exists when all 'acq_nb_frames' have been acquired.
        This method is automatically (re)spwaned after each start/trigger call (if not already alive).
        """
        _logger.debug("reading: started")

        last_nb_frames = 0
        pipeline = self._dev.current_pipeline

        while True:
            new_state = False
            new_image_acquired = False
            status = {}
            try:
                status["state"] = self._dev.state
                status["nb_frames_acquired"] = self._dev.nb_frames_acquired
                status["nb_frames_xferred"] = self._dev.nb_frames_xferred
                # If nb_frames_acquired not avaialble for this detector
                if status["nb_frames_acquired"] < status["nb_frames_xferred"]:
                    status["nb_frames_acquired"] = status["nb_frames_xferred"]
                # TODO support multiple processings
                progress_counters = pipeline.progress_counters
            except Exception:
                status["state"] = State.FAULT
                status["nb_frames_xferred"] = 0
                new_state = True
                raise

            _logger.debug(f"reading: status = {status}")

            # Exit reading loop when camera is ready
            if status["state"] != State.RUNNING:
                new_state = True

            # Use the max of nb_frames_acquired and nb_frames_xferred as progress indicator
            nb_frames = max(status["nb_frames_acquired"], status["nb_frames_xferred"])
            if nb_frames > last_nb_frames:
                new_image_acquired = True
                last_nb_frames = nb_frames

            if new_state or new_image_acquired:
                _logger.debug(f"status: {status}")
                if self.channels:
                    self._emit_status(status, progress_counters)

            # Raise if detector is in fault
            if status["state"] == State.FAULT:
                _logger.error(f"LIMA2 {self.name} is in Fault state")
                raise RuntimeError(f"LIMA2 {self.name} is in Fault state")

            # Check that new images were acquired
            if new_image_acquired:
                # Check if next iteration is allowed
                if not self._ready_event.is_set():
                    if self.prepare_once:
                        _logger.debug(
                            f"nb_frames {nb_frames} / {self._current_wait_frame_id}"
                        )
                        if nb_frames >= self._current_wait_frame_id:
                            self._ready_event.set()
                            _logger.debug("set ready event")
                else:
                    _logger.debug("ready event is set")

            _logger.debug(f"reading: status = {status}")

            # if status["state"] in [State.CLOSING, State.IDLE] and pipeline.is_finished:
            if status["state"] in [State.CLOSING, State.IDLE]:
                self._emit_status(status, progress_counters)
                break

            # Sleep between [10, 100] milliseconds depending on expo time
            gevent.sleep(min(0.1, max(self.acq_params["expo_time"] / 10.0, 0.01)))

        _logger.debug("reading: finished")

    def get_acquisition_metadata(self, timing=None):
        """
        Returns time-dependent meta data related to this device.
        """
        tmp_dict = super().get_acquisition_metadata(timing=timing)
        if timing == self.META_TIMING.END:
            if tmp_dict is None:
                tmp_dict = dict()

            # TODO: save all the information (currently, saving all ctrl_params is not suppported by the NxWriter)
            tmp_dict["acq_params"] = self.ctrl_params["ctrl"]

            # from copy import deepcopy
            # tmp_dict = deepcopy(self.ctrl_params)

        return tmp_dict


class Lima2ProcessingSlave(AcquisitionSlave):
    pass
