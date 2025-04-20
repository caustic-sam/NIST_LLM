# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

import json
import gevent
from enum import Enum, auto

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


class AcqState(Enum):
    """Server-side state machine states"""

    idle = 0
    prepared = auto()
    running = auto()
    stopped = auto()
    fault = auto()
    terminate = auto()


class Control(Device):
    """Mock a device server of control subsystem"""

    ctrl_params_schema = class_property(dtype=str)

    attach_debugger = device_property(dtype=bool, default_value=False)

    def init_device(self):
        super(Device, self).init_device()
        self.set_state(DevState.ON)
        self.__state = AcqState.idle
        self.__nb_frames_acquired = 0
        self.__ctrl_params = {}
        self.__timer = None
        self.acq_state.set_change_event(True, False)
        #     "acq": {
        #         "nb_frames": 1000,
        #         "expo_time": 10,
        #         "latency_time": 990,
        #         "mode": "normal",
        #     },
        #     "img": {
        #         "binning": {"x": 1, "y": 1},
        #         "roi": {"topleft": {"x": 0, "y": 0}, "dimensions": {"x": 0, "y": 0}},
        #         "flip": "none",
        #     },
        #     "shut": {"mode": "manual"},
        #     "accu": {"nb_frames": 1, "expo_time": 1000, "saturated_threshold": 0},
        #     "vid": {"auto_exposure_mode": "off"},
        #     "xfer": {
        #         "alignment": {
        #             "col_alignment": 1,
        #             "row_alignment": 8,
        #             "header": 0,
        #             "footer": 0,
        #         },
        #         "time_slice": {"start": 0, "count": 1000, "stride": 1},
        #     },
        #     "det": {
        #         "image_source": "generator",
        #         "generator": {
        #             "generator_type": "gauss",
        #             "gauss": {
        #                 "peaks": [
        #                     {"x0": 1024.0, "y0": 1024.0, "fwhm": 128.0, "max": 100.0}
        #                 ],
        #                 "grow_factor": 0.0,
        #             },
        #             "diffraction": {
        #                 "gauss": {
        #                     "peaks": [
        #                         {"x0": 5.0, "y0": 5.0, "fwhm": 0.5, "max": 100.0}
        #                     ],
        #                     "grow_factor": 0.0,
        #                 },
        #                 "x0": 1024.0,
        #                 "y0": 1024.0,
        #                 "source_pos_x": 5.0,
        #                 "source_pos_y": 5.0,
        #                 "source_speed_x": 0.0,
        #                 "source_speed_y": 0.0,
        #             },
        #             "pixel_type": "gray8",
        #         },
        #         "nb_prefetch_frames": 10,
        #     },
        # }

    def __update_state(self, state: AcqState):
        self.__state = state
        self.push_change_event("acq_state", self.__state.value)

    @attribute(
        label="ctrl_params",
        dtype=str,
        access=AttrWriteType.READ_WRITE,
        doc="Acquisition parameters",
    )
    def ctrl_params(self):
        return json.dumps(self.__ctrl_params)

    @ctrl_params.write
    def ctrl_params(self, ctrl_params):
        self.__ctrl_params = json.loads(ctrl_params)

    @attribute(
        label="det_info",
        dtype=str,
        access=AttrWriteType.READ,
        doc="Detector information",
    )
    def det_info(self):
        return {
            "plugin": "Simulator",
            "model": "Top Model",
            "pixel_size": {"x": 5e-1, "y": 5e-1},
            "expo_time_range": [1, 10000000],
            "latency_time_range": [0, 1000000],
            "trigger_modes": ["software"],
            "dimensions": {"x": 2048, "y": 2048},
        }

    @attribute(
        label="acq_state",
        dtype=AcqState,
        access=AttrWriteType.READ,
        doc="State of the control subsystem",
    )
    def acq_state(self):
        return self.__state.value

    @attribute(label="nb_frames_acquired", dtype=int, access=AttrWriteType.READ)
    def nb_frames_acquired(self):
        return self.__nb_frames_acquired

    @command(dtype_in=str, doc_in="The UUID of the acquisition")
    def Prepare(self, uuid):
        """Prepare the acquisition"""
        assert self.__ctrl_params["nb_frames"] > 0
        assert self.__ctrl_params["expo_time"] > 0.0
        gevent.sleep(1)
        self.__nb_frames_acquired = 0
        self.__update_state(AcqState.prepared)

    @command
    def Start(self):
        """Start the acquisition"""
        assert self.__state == AcqState.prepared
        assert self.__ctrl_params
        self.__update_state(AcqState.running)

        nb_frames = self.__ctrl_params["nb_frames"]
        expo_time = self.__ctrl_params["expo_time"]

        def run_acquisition(count):
            self.__nb_frames_acquired = count
            if count == nb_frames - 1:
                self.__update_state(AcqState.idle)

        self.__timer = Timer(
            run_acquisition,
            expo_time,
            nb_frames,
        )

    @command
    def Stop(self):
        """Stop the acquisition"""
        if self.__state == AcqState.running:
            if self.__timer:
                self.__timer.stop()
            gevent.sleep(1)
            self.__update_state(AcqState.idle)

    @command
    def Close(self):
        """Cleanup the acquisition"""
        self.__update_state(AcqState.idle)

    @command
    def simulate_acq(self):
        """Pretend a frame was acquired"""
        self.__nb_frames_acquired += 1
