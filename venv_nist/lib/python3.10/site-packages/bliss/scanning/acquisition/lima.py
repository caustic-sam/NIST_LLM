# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import os
from itertools import count, repeat
import gevent
from gevent import event
from louie import dispatcher
import numpy
from collections import OrderedDict

from bliss.common.data_store import get_default_data_store
from bliss.common.logtools import log_warning, log_debug
from bliss.scanning.chain import AcquisitionMaster
from bliss.scanning.channel import LimaAcquisitionChannel
from bliss.common.tango import get_fqn
from bliss.scanning.acquisition.counter import IntegratingCounterAcquisitionSlave
from bliss.common.os_utils import makedirs
from blissdata.lima.client import prepare_next_lima_acquisition


LIMA_DTYPE = {
    (0, 2): numpy.uint16,
    (1, 2): numpy.int16,
    (0, 4): numpy.uint32,
    (1, 4): numpy.int32,
    (0, 1): numpy.uint8,
    (1, 1): numpy.int8,
}


class LimaAcquisitionMaster(AcquisitionMaster):
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

    """

    def __init__(self, device, ctrl_params=None, **acq_params):

        # === auto-complet missing controller parameters ======================================
        ctrl_params = self.init_ctrl_params(device, ctrl_params)

        # === deal with missing or incompatible acquisition parameters ========================

        # use validation schemes to check received acquisition parameters
        # warning: this operation requires the completed controller parameters dict
        self.acq_params = OrderedDict(self.validate_params(acq_params, ctrl_params))

        # pop the non-lima (tango) acquisition parameters.
        prepare_once = self.acq_params.pop("prepare_once")
        start_once = self.acq_params.pop("start_once")
        # 'wait_frame_id' is a list of 'last_image_acquired' frame numbers
        # for which this acquisition object should wait before proceeding to
        # the next scan iteration (see __iter__).
        wait_frame_id = self.acq_params.pop("wait_frame_id", None)

        # 'acq_nb_frames' is the number of frames for which a camera is prepared (i.e. for one prepare).
        acq_nb_frames = self.acq_params["acq_nb_frames"]
        self.__acq_nb_frames = max(acq_nb_frames, 1)  # handles acq_nb_frames == 0

        # === ABOUT TRIGGER MODS ==============================================================
        # INTERNAL_TRIGGER: software trigger, one trigger for the acquisition of 'acq_nb_frames'
        # INTERNAL_TRIGGER_MULTI: software trigger, one trigger per frame (to be repeated 'acq_nb_frames' times)
        # EXTERNAL_TRIGGER: hardware trigger, one trigger for the acquisition of 'acq_nb_frames'
        # EXTERNAL_TRIGGER_MULTI: hardware trigger, one trigger per frame (to be repeated 'acq_nb_frames' times)
        # EXTERNAL_GATE: hardware trigger, one gate signal per frame, the gate period is the exposure time
        # EXTERNAL_START_STOP: hardware trigger, two pulse per frame, first pulse starts exposure, second pulse ends exposure
        acq_trigger_mode = self.acq_params["acq_trigger_mode"]

        # deal with 'ONE_FILE_PER_SCAN' mode
        if ctrl_params.get("saving_frame_per_file") == -1:
            ctrl_params["saving_frame_per_file"] = self.__acq_nb_frames

        # decide this acquisition object's trigger type
        # (see 'trigger_slaves' called by the acqObj of the upper node in acq chain)
        trigger_type = (
            AcquisitionMaster.SOFTWARE
            if acq_trigger_mode.startswith("INTERNAL")
            else AcquisitionMaster.HARDWARE
        )

        # === handle possible acquisition modes and discard invalid cases ======================

        if not prepare_once and start_once:
            raise ValueError("'start_once' cannot be True if 'prepare_once' is False ")

        if not prepare_once and acq_trigger_mode == "INTERNAL_TRIGGER_MULTI":
            prepare_once = True
            msg = "WARNING: 'prepare_once' have been automatically changed to 'True' because using INTERNAL_TRIGGER_MULTI"
            log_warning(self, msg)

        self.__wait_frame_id_iterator = None
        self.__expected_total_frames_number = None
        self.__force_top_master_one_iter_max = False
        self.__one_frame_per_scan_iter = False

        mdict = {
            "prepare_once": prepare_once,
            "start_once": start_once,
            "acq_trigger_mode": acq_trigger_mode,
        }

        if not prepare_once and not start_once:

            if acq_nb_frames == 0:
                # With INTERNAL_TRIGGER or EXTERNAL_TRIGGER the camera
                # would never stop taking images on the first iter and would remain
                # RUNNING so that this object would stay blocked in reading
                # and would never proceed to next iter.
                # With the XXX_MULTI modes the camera may behave like if acq_nb_frames = 1
                # but salves acquisition objects would never exit because they would inherite
                # npoints = acq_nb_frames = 0 from this masterobject and therefore this object would
                # hangs on wait_slaves_prepare and would never proceed to next iteration.
                msg = f"{mdict} is incompatible with 'acq_nb_frames' = 0 (must be > 0)"
                raise ValueError(msg)

            wfid = self.__acq_nb_frames - 1
            if wait_frame_id is None:
                # provide a default by repeating the per iteration frame number.
                # scan iterations are not known here so we cannot create a finite iterator.
                self.__wait_frame_id_iterator = repeat(wfid)
                # prevent infinite iterations if this object is a top master
                self.__force_top_master_one_iter_max = True

            elif wait_frame_id is iter(wait_frame_id):
                # if wait_frame_id is an iterator take it but we cannot know if it is finite or not.
                # !!! WARNING this could lead to infinite iterations if this object is a top master !!!
                self.__wait_frame_id_iterator = wait_frame_id
            else:
                # if wait_frame_id is a finite iterable, check is valid
                if len(set(wait_frame_id)) != 1:
                    msg = "With 'prepare_once = False', elements of 'wait_frame_id' must be all equals"
                    raise ValueError(msg)

                if wait_frame_id[0] != wfid:
                    msg = (
                        "'wait_frame_id' elements must be equal to 'acq_nb_frames - 1'"
                    )
                    raise ValueError(msg)

                self.__wait_frame_id_iterator = iter(wait_frame_id)
                self.__expected_total_frames_number = self.__acq_nb_frames * len(
                    wait_frame_id
                )

        elif prepare_once and not start_once:
            if acq_trigger_mode != "INTERNAL_TRIGGER_MULTI":
                msg = f"{mdict} is incompatible, only 'INTERNAL_TRIGGER_MULTI' is valid"
                raise ValueError(msg)

            if acq_nb_frames == 0:
                # in the INTERNAL_TRIGGER_MULTI case (with acq_nb_frames = 0) the simulator state comes back
                # to ready after each exposure, but it can takes another start without a new prepare
                # and last_image_acquired increments one by one (eg [0, 1, 2, ...])
                # reading loop exits after each frame but is respawned after each start
                self.__wait_frame_id_iterator = count()
            else:
                self.__wait_frame_id_iterator = iter(range(acq_nb_frames))
                self.__expected_total_frames_number = acq_nb_frames

            self.__one_frame_per_scan_iter = True

        elif prepare_once and start_once:
            valid_trigger_modes = [
                "INTERNAL_TRIGGER",
                "EXTERNAL_TRIGGER",
                "EXTERNAL_TRIGGER_MULTI",
                "EXTERNAL_GATE",
                "EXTERNAL_START_STOP",
            ]
            if acq_trigger_mode not in valid_trigger_modes:
                msg = f"{mdict} is incompatible, trigger mode should be in {valid_trigger_modes}"
                raise ValueError(msg)

            if acq_nb_frames == 0:
                if acq_trigger_mode in ["INTERNAL_TRIGGER", "EXTERNAL_TRIGGER"]:
                    self.__wait_frame_id_iterator = iter([numpy.inf])
                elif wait_frame_id is None:
                    self.__wait_frame_id_iterator = count()
                    self.__one_frame_per_scan_iter = True
            else:
                if wait_frame_id is None:
                    # suppose one trigger per frame
                    wait_frame_id = range(acq_nb_frames)

                elif wait_frame_id is iter(wait_frame_id):
                    # check given wait_frame_id is a finite list (i.e. not a pure iterator)
                    msg = f"With '{acq_trigger_mode}' and 'acq_nb_frames' != 0, "
                    msg += "'wait_frame_id' must be a finite list"
                    raise ValueError(msg)

                elif wait_frame_id[-1] + 1 != acq_nb_frames:
                    # check that last value of the given wait_frame_id list corresponds to the last frame number
                    raise ValueError(
                        "'wait_frame_id' last value does not match 'acq_nb_frames'"
                    )

                if len(wait_frame_id) == acq_nb_frames:
                    self.__one_frame_per_scan_iter = True

                self.__wait_frame_id_iterator = iter(wait_frame_id)
                self.__expected_total_frames_number = acq_nb_frames

        # =======================================================================================

        # Note: npoints is assimilated to the number of frames for which a camera is prepared (i.e. 'acq_nb_frames')
        # It is not always equal to the number of iterations of the scanning procedure
        # Also slaves objects, linked to this lima master, will inherite their 'self.npoints' value from this one.
        AcquisitionMaster.__init__(
            self,
            device,
            name=device.name,
            npoints=acq_nb_frames,
            trigger_type=trigger_type,
            prepare_once=prepare_once,
            start_once=start_once,
            ctrl_params=ctrl_params,
        )

        self._image_channel = None
        self._latency = self.acq_params["latency_time"]
        self._ready_for_next_iter = event.Event()
        self._ready_for_next_iter.set()
        self.__current_iteration_index = 0
        self.__current_wait_frame_id = 0
        self.__acquired_frames_offset = 0
        self.__number_of_acquired_frames = 0
        self.__live_stopped = False
        self._server_url = get_fqn(self.device.proxy)

        mdict.update(self.acq_params)
        log_debug(self.device, "acq_params: %s", mdict)

    @staticmethod
    def get_param_validation_schema():

        # lima_ctrl_param_schema = {}

        lima_master_base_schema = {
            "prepare_once": {"type": "boolean", "default": False},
            "start_once": {"type": "boolean", "default": False},
            "acq_nb_frames": {"type": "int", "default": 1},
            "acq_expo_time": {"type": "numeric", "default": 1},
            "acq_trigger_mode": {"type": "string", "default": "INTERNAL_TRIGGER"},
            "latency_time": {"type": "numeric", "default": 0},
            "wait_frame_id": {"default": None, "nullable": True},
            "saving_mode": {"type": "string"},
            "saving_statistics_history": {
                "type": "numeric",
                "default_setter": lambda x: x["acq_nb_frames"],
            },
        }

        lima_master_no_acc_schema = {
            "acq_mode": {"default": "SINGLE", "type": "string", "value": "SINGLE"}
        }

        lima_master_acc_schema = {
            "acq_mode": {"type": "string", "required": True},
            "acc_time_mode": {"default": "LIVE", "allowed": ["LIVE"]},
        }

        lima_master_concat_schema = {
            "acq_mode": {"type": "string", "required": True},
            "concat_nb_frames": {"type": "int", "default": 1},
        }

        lima_master_schema = {
            "acq_params": {
                "type": "dict",
                "oneof": [
                    {
                        "dependencies": {"acq_params.acq_mode": "SINGLE"},
                        "schema": {
                            **lima_master_base_schema,
                            **lima_master_no_acc_schema,
                        },
                    },
                    {
                        "dependencies": {"acq_params.acq_mode": "ACCUMULATION"},
                        "schema": {**lima_master_base_schema, **lima_master_acc_schema},
                    },
                    {
                        "dependencies": {"acq_params.acq_mode": "CONCATENATION"},
                        "schema": {
                            **lima_master_base_schema,
                            **lima_master_concat_schema,
                        },
                    },
                ],
            },
            "ctrl_params": {
                "type": "dict",
                #  "schema": lima_ctrl_param_schema,
                "default": {},
            },
        }
        return lima_master_schema

    @property
    def fast_synchro(self):
        return self.device.camera.synchro_mode == "TRIGGER"

    @property
    def number_of_acquired_frames(self):
        """return the number of currently acquired frames (over the entire acquisition process)"""
        return self.__number_of_acquired_frames

    def __iter__(self):
        while True:
            try:
                self.__current_wait_frame_id = next(self.__wait_frame_id_iterator)
                log_debug(
                    self.device,
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

    def add_counter(self, counter):
        if counter in self._counters:
            return

        if counter.name != "image":
            raise ValueError("Lima master only supports the 'image' counter")

        # Create the channel now, but info will be known at the very last moment
        # before starting the scan, i.e. on the first call to self.prepare().
        self._image_channel = LimaAcquisitionChannel(f"{self.name}:{counter.name}")
        self.channels.append(self._image_channel)
        self._counters[counter].append(self.channels[-1])

    @property
    def save_flag(self):
        return bool(self._image_channel)

    def set_device_saving(self, directory, prefix, force_no_saving=False):
        if not self.save_flag or force_no_saving:
            self.acq_params["saving_mode"] = "NOSAVING"
            return

        self.acq_params["saving_mode"] = self.acq_params.setdefault(
            "saving_mode", "AUTO_FRAME"
        )
        assert self.acq_params["saving_mode"] != "NOSAVING"

        # get saving directory and handle a possible mapping of the root path
        # check_validity option returns True if the root exist or if there is no mapping
        (
            self.acq_params["saving_directory"],
            validity,
        ) = self.device.get_mapped_path(directory, check_validity=True)
        if validity:  # if path is valid, create directory (if it doesnt exist yet)
            makedirs(self.acq_params["saving_directory"], exist_ok=True)

        self._unmapped_path = directory
        self.acq_params.setdefault("saving_prefix", prefix)

    def _stop_video_live(self):
        # external live preview processes may use the cam_proxy in video_live or in an infinit loop (acq_nb_frames=0)
        # if it is the case, stop the camera/video_live only once (at the 1st wait_ready called before prepare)
        if not self.__live_stopped:
            if self.device.proxy.acq_nb_frames == 0:  # target live acquisition
                if self.device.proxy.acq_status == "Running":
                    self.device.proxy.video_live = False
                    self.device.proxy.stopAcq()
            self.__live_stopped = True  # allow only at first call

    def prepare(self):
        self.device.ensure_minimal_version()

        if self.prepare_once and self.__current_iteration_index != 0:
            return

        # Advertise on Redis about new acquisition so previous image references are invalidated
        self.acq_number = prepare_next_lima_acquisition(
            get_default_data_store(), self._server_url
        )

        # ensure bpm is stopped (see issue #1685)
        # it is important to stop it in prepare, and to have it
        # started if needed in "start" since prepare is executed
        # before start
        self.device.bpm.stop()

        # make sure that parameters are in the good order for lima:
        self.acq_params.move_to_end("acq_mode", last=False)
        if "saving_prefix" in self.acq_params:
            self.acq_params.move_to_end("saving_prefix", last=True)

        for param_name, param_value in self.acq_params.items():
            if param_value is not None:
                # need to have a difference with MANUAL saving and NOSAVING in bliss
                # but with Lima device there is only MANUAL
                if param_name == "saving_mode" and param_value == "NOSAVING":
                    param_value = "MANUAL"
                try:
                    setattr(self.device.proxy, param_name, param_value)
                except AttributeError:
                    # FIXME: acq_params do not only contains tango attr...
                    log_warning(
                        self,
                        "Can't set attribute '%s' to the Tango device %s. Attribute skipped.",
                        param_name,
                        self.device.proxy.name(),
                    )

        self.device.proxy.video_source = "LAST_IMAGE"

        self.wait_slaves_prepare()

        self.device.proxy.video_active = (
            self.ctrl_params["saving_managed_mode"] == "SOFTWARE"
        )

        log_debug(self.device, "proxy.prepareAcq")
        self.device.prepareAcq()

        self._latency = self.device.proxy.latency_time

        if self.__current_iteration_index == 0:
            if self._image_channel:
                # Finalize image channel info on the first prepare()
                signed, depth, w, h = self.device.proxy.image_sizes
                self._image_channel.configure(
                    dtype=LIMA_DTYPE[(signed, depth)],
                    shape=(int(h), int(w)),
                    server_url=self._server_url,
                    buffer_max_number=self.device.proxy.buffer_max_number,
                    acquisition_offset=self.acq_number,
                    frames_per_acquisition=self.acq_params["acq_nb_frames"],
                )
                if self.acq_params["saving_mode"] != "NOSAVING":
                    self._image_channel.configure_saving(
                        **self._get_saving_description()
                    )

    def start(self):
        if self.trigger_type == AcquisitionMaster.SOFTWARE and self.parent:
            # In that case we expect that the parent acqObj will take care of calling
            # 'self.trigger' via its 'trigger_slaves' method
            # (!!! expecting that parent.trigger() method's uses 'trigger_slaves' !!!)
            return

        self.trigger()

    def stop(self):
        log_debug(self.device, "proxy.stopAcq")
        self.device.stopAcq()

    def wait_ready(self):
        self._stop_video_live()
        log_debug(self.device, "ready_for_next_iter WAIT")
        self._ready_for_next_iter.wait()
        self._ready_for_next_iter.clear()
        log_debug(self.device, "ready_for_next_iter CLEAR")

    def trigger(self):
        self.trigger_slaves()

        if self.__current_iteration_index > 0 and self.start_once:
            return

        log_debug(self.device, "proxy.startAcq")
        self.device.startAcq()
        self.spawn_reading_task(rawlink_event=self._ready_for_next_iter)

        # event used in custom scans (like fscanloop)
        dispatcher.send("lima_started", self)

    def _get_lima_status(self):
        keys = [
            "acq_status",
            "ready_for_next_image",
            "buffer_max_number",
            "last_image_acquired",
            "last_image_ready",
            "last_counter_ready",
            "last_image_saved",
        ]
        values = [x.value for x in self.device.proxy.read_attributes(keys)]
        status = dict(zip(keys, values))
        status["acq_state"] = status.pop("acq_status").lower()
        return status

    def _get_saving_description(self):
        saving_managed_mode = self.ctrl_params["saving_managed_mode"]

        file_format = self.ctrl_params["saving_format"]
        if file_format.lower().startswith("hdf5"):
            file_format = "hdf5"
        elif file_format.lower().startswith("edf"):
            file_format = "edf"
        elif file_format.lower().startswith("cbf"):
            file_format = "cbf"
        else:
            raise NotImplementedError(
                f"There is no schema defined for {file_format} format"
            )

        saving_suffix = self.ctrl_params["saving_suffix"]
        saving_prefix = self.acq_params["saving_prefix"]
        saving_index_format = self.device.proxy.saving_index_format
        if saving_managed_mode == "HARDWARE":
            # Assume Dectris hardware saving
            subdir_format = f"{saving_prefix}_data_{saving_index_format}{saving_suffix}"
        else:
            subdir_format = f"{saving_prefix}{saving_index_format}{saving_suffix}"
        file_path = os.path.join(self._unmapped_path, subdir_format)

        file_id_offset = 0
        if file_format == "hdf5":
            if saving_managed_mode == "HARDWARE":
                # Assume Dectris hardware saving
                data_path = "/entry/data/data"
                # Dectris starts counting from 1 instead of 0 like lima does
                file_id_offset = 1
            else:
                data_path = f"/entry_0000/{self.device.proxy.user_instrument_name}/{self.device.proxy.user_detector_name}/data"
        else:
            data_path = None

        return {
            "file_offset": file_id_offset,
            "frames_per_file": self.ctrl_params["saving_frame_per_file"],
            "file_format": file_format,
            "file_path": file_path,
            "data_path": data_path,
        }

    def __emit_new_status(self, status):
        progress_keys = ["last_image_ready", "last_image_saved", "acq_state"]
        self.emit_progress_signal({k: status[k] for k in progress_keys})
        if self._image_channel and status["last_image_ready"] >= 0:
            payload = {
                "last_index": status["last_image_ready"],
                "last_index_saved": status["last_image_saved"],
            }
            self._image_channel.emit(payload)

    def reading(self):
        """Gather and emit lima status while camera is running (acq_state and last image info).
        Also sets the '_ready_for_next_iter' when it is valid to proceed to the next scan iteration.
        For each 'prepare', camera is configured for the acquisition of 'acq_nb_frames'
        and this method exits when all 'acq_nb_frames' have been acquired.
        This method is automatically (re)spwaned after each start/trigger call (if not already alive).
        """

        last_acquired_frame_number = -1
        last_ready_frame_number = -1
        last_acq_state = None

        log_debug(self.device, "ENTER reading loop")

        while True:

            # a flag to decide if the lima status should be emitted
            do_emit_new_status = False

            # read lima proxy status
            status = self._get_lima_status()

            # check if acq_state has changed
            if status["acq_state"] != last_acq_state:
                last_acq_state = status["acq_state"]
                do_emit_new_status = True

            # check if new image_ready
            if status["last_image_ready"] != last_ready_frame_number:
                last_ready_frame_number = status["last_image_ready"]
                do_emit_new_status = True

            # check if new image_acquired
            delta_acquired = status["last_image_acquired"] - last_acquired_frame_number
            if delta_acquired > 0:
                self.__number_of_acquired_frames += delta_acquired
                last_acquired_frame_number = status["last_image_acquired"]
                log_debug(self.device, "last_acq_frame: %s", last_acquired_frame_number)

            # update status info if necessary
            if not self.prepare_once:
                # update frames numbers taking into account frames of the previous sequences
                for key in (
                    "last_image_acquired",
                    "last_image_ready",
                    "last_counter_ready",
                    "last_image_saved",
                ):
                    status[key] += self.__acquired_frames_offset

            # emit status if necessary
            if do_emit_new_status:
                log_debug(self.device, "emit new status: %s", status)
                self.__emit_new_status(status)

            # raise if camera is in fault
            if last_acq_state == "fault":
                try:
                    err_msg = f" ({self.device.proxy.acq_status_fault_error})"
                except Exception:
                    log_debug(
                        self.device,
                        "Failed while retrieving error message",
                        exc_info=True,
                    )
                    err_msg = ""
                raise RuntimeError(
                    f"Detector {self.device.name} ({self.device.proxy.dev_name()}) is in Fault state{err_msg}"
                )

            # check if next iteration is allowed
            # NB: self._ready_for_next_iter is set automatically when the reading task ends !!!
            if self.fast_synchro and self.__one_frame_per_scan_iter:
                if status["ready_for_next_image"]:
                    if last_acq_state != "ready":
                        log_debug(
                            self.device,
                            "ready_for_next_iter SET (on ready_for_next_image)",
                        )
                        self._ready_for_next_iter.set()
            elif last_acquired_frame_number > self.__current_wait_frame_id:
                msg = f"Last acquired frame number ({last_acquired_frame_number})"
                msg += f" is greater than current wait frame id ({self.__current_wait_frame_id})!\n"
                msg += "It can happen if the detector has received more hardware triggers per scan iteration than expected.\n"
                msg += "Please check that acq param 'wait_frame_id' is compatible with the hardware triggers generation pattern\n"
                msg += "and that hw triggers are not coming too fast between two scan iterations."
                raise RuntimeError(msg)
            elif last_acquired_frame_number == self.__current_wait_frame_id:
                if self.prepare_once:
                    if delta_acquired > 0:
                        if last_acq_state != "ready":
                            log_debug(
                                self.device,
                                "ready_for_next_iter SET (on acq_frame == wait_frame == %s and prepare_once True)",
                                last_acquired_frame_number,
                            )
                            self._ready_for_next_iter.set()

                elif last_acq_state == "ready":
                    self.__acquired_frames_offset += self.__acq_nb_frames
                    log_debug(
                        self.device,
                        "acquired_frames_offset: %s",
                        self.__acquired_frames_offset,
                    )

            # exit reading loop when camera is ready
            if last_acq_state == "ready":
                break

            # sleep between [10, 100] milliseconds depending on expo time
            gevent.sleep(min(0.1, max(self.acq_params["acq_expo_time"] / 10.0, 0.01)))

        log_debug(self.device, "EXIT reading loop")

    def get_acquisition_metadata(self, timing=None):
        meta_dict = super().get_acquisition_metadata(timing=timing)
        if timing == self.META_TIMING.END:
            if meta_dict is None:
                meta_dict = dict()
            meta_dict["acq_parameters"] = self.acq_params
            meta_dict["ctrl_parameters"] = {**self.ctrl_params}
        return meta_dict


class RoiCountersAcquisitionSlave(IntegratingCounterAcquisitionSlave):
    def prepare_device(self):
        self.device._proxy.clearAllRois()
        self.device._proxy.start()  # after the clearAllRois (unlike 'roi2spectrum' proxy)!
        self.device.upload_rois()

    def start_device(self):
        pass

    def stop_device(self):
        self.device._proxy.Stop()


class RoiProfileAcquisitionSlave(IntegratingCounterAcquisitionSlave):
    def prepare_device(self):
        self.device._proxy.start()  # before the clearAllRois (unlike 'roicounter' proxy) !
        self.device._proxy.clearAllRois()
        self.device.upload_rois()

    def start_device(self):
        pass

    def stop_device(self):
        self.device._proxy.Stop()


class BpmAcquisitionSlave(IntegratingCounterAcquisitionSlave):
    def prepare_device(self):
        pass

    def start_device(self):
        # it is important to start bpm in "start" and not in "prepare",
        # since camera prepare is stopping it to avoid problems like
        # issue #1685
        self.device.start()

    def stop_device(self):
        pass
