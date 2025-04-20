# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

import json
import gevent

from tango.server import (
    Device,
    attribute,
    command,
    class_property,
    device_property,
    AttrWriteType,
)
from tango import DevState

from lima2.server.timer import Timer
from lima2.server.control import AcqState


class Processing(Device):
    def init_device(self):
        super(Device, self).init_device()
        self.__progress_counters = {"nb_frames_processed": 0, "nb_frames_saved": 0}

    @attribute(
        label="progress_counters",
        dtype=str,
        access=AttrWriteType.READ,
        doc="Progress counters",
    )
    def progress_counters(self):
        return json.dumps(self.__progress_counters)


class Receiver(Device):
    """Mock a device server of receiver subsystem"""

    acq_params_schema = class_property(dtype=str)
    proc_params_schema = class_property(dtype=str)

    attach_debugger = device_property(dtype=bool, default_value=False)

    mpi_rank = device_property(dtype=int, default_value=0)

    def init_device(self):
        super(Device, self).init_device()
        self.set_state(DevState.ON)
        self.__state = AcqState.idle
        self.__nb_frames_xferred = 0
        self.__on_end_acq = None
        self.__acq_params = {}
        self.__proc_params = {}
        self.__timer = None
        self.__pipelines = []
        self.nb_frames_xferred.set_change_event(True, False)
        self.acq_state.set_change_event(True, False)

    def __update_state(self, state: AcqState):
        self.__state = state
        self.push_change_event("acq_state", self.__state.value)

    @attribute(
        label="recv_rank",
        dtype=int,
        access=AttrWriteType.READ,
        doc="rank in receivers communicator",
    )
    def recv_rank(self):
        return max(0, self.mpi_rank - 1)

    @attribute(
        label="acq_params",
        dtype=str,
        access=AttrWriteType.READ_WRITE,
        doc="Acquisition parameters",
    )
    def acq_params(self):
        return json.dumps(self.__acq_params)

    @acq_params.write
    def acq_params(self, acq_params):
        self.__acq_params = json.loads(acq_params)

    @attribute(
        label="proc_params",
        dtype=str,
        access=AttrWriteType.READ_WRITE,
        doc="Processing parameters",
    )
    def proc_params(self):
        return json.dumps(self.__proc_params)

    @proc_params.write
    def proc_params(self, proc_params):
        self.__proc_params = json.loads(proc_params)

    @attribute(
        label="acq_state",
        dtype=AcqState,
        access=AttrWriteType.READ,
        doc="State of the receiver subsystem",
    )
    def acq_state(self):
        return self.__state.value

    @attribute(
        label="nb_frames_xferred",
        dtype=int,
        access=AttrWriteType.READ,
        doc="Number of frames transferred",
    )
    def nb_frames_xferred(self):
        return self.__nb_frames_xferred

    @attribute(
        label="current_pipeline",
        dtype=str,
        access=AttrWriteType.READ,
        doc="Current processing pipeline",
    )
    def current_pipeline(self):
        if self.__pipelines:
            return self.__pipelines[-1]
        else:
            return ""

    @attribute(
        label="pipelines",
        dtype=(str,),
        max_dim_x=256,
        access=AttrWriteType.READ,
        doc="All pipelines",
    )
    def pipelines(self):
        return self.__pipelines

    @command(dtype_in=str)
    def Prepare(self, uuid):
        assert self.__acq_params["nb_frames"] > 0
        assert self.__acq_params["expo_time"] > 0.0
        gevent.sleep(1)
        self.__update_state(AcqState.prepared)

    @command
    def Start(self):
        assert self.__state == AcqState.prepared
        assert self.__acq_params
        self.__update_state(AcqState.running)

        nb_frames = self.__acq_params["nb_frames"]
        expo_time = self.__acq_params["expo_time"]

        def run_acquisition(count):
            self.__nb_frames_xferred = count
            if count == nb_frames - 1:
                self.__update_state(AcqState.idle)
                self.push_change_event("nb_frames_xferred", count)

        self.__timer = Timer(
            run_acquisition,
            expo_time,
            nb_frames,
        )

    @command
    def Stop(self):
        if self.__state == AcqState.running:
            if self.__timer:
                self.__timer.stop()
            gevent.sleep(1)
            self.__update_state(AcqState.idle)

    @command
    def Close(self):
        self.__update_state(AcqState.idle)

    def fail(self):
        raise RuntimeError("Simulating error")

    @command
    def simulate_xfer(self):
        """Pretend a frame was transferred"""
        self.__nb_frames_xferred += 1

    @command(dtype_in=str)
    def create_pipeline(self, uuid: str):
        """Create a mock pipeline"""
        self.__pipelines.append(uuid)

    @command
    def erasePipeline(self, uuid: str):
        self.__pipelines.remove(uuid)
