# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numbers
import gevent
import requests
import json
import base64
import numpy
import fabio

from bliss.common.utils import typecheck
from bliss.common.counter import SamplingCounter
from bliss.common.protocols import counter_namespace
from bliss.common.user_status_info import status_message

from bliss.controllers.lima2.tabulate import tabulate
from bliss.controllers.lima2.counter import FrameCounter
from bliss.controllers.lima2.controller import (
    DetectorController,
    DetectorStatusController,
)
from bliss.controllers.lima2.settings import Settings, setting_property

DECTRIS_TO_NUMPY = {"<u4": numpy.uint32, "<f4": numpy.float32}


class Detector:
    """Dectris Eiger2 detector user interface"""

    def __init__(self, device):
        self._det_cc = DetectorStatusController(device)
        self._frame_cc = DetectorController(device)

        self._config = device._config.get("dectris", {})
        self._dcu_ip = self._config.get("ip_address", None)

        self._temperature_cnt = SamplingCounter(
            "temperature", self._det_cc, unit="degC"
        )
        self._humidity_cnt = SamplingCounter("humidity", self._det_cc, unit="%")
        self._raw_frame_cnt = FrameCounter(
            "raw_frame", device._frame_cc, ("recvs", "saving"), file_only=True
        )

        class Acquisition(Settings):
            """
            {
                'thresholds': [{
                    'energy': 4020.5,
                    'enabled': True
                }, {
                    'energy': 4020.5,
                    'enabled': True
                }],
                'trigger_start_delay': 0.0,
                'roi': 'full',
                'nb_pipeline_threads': 1
            }
            """

            def __init__(self, device):
                self._device = device
                self._params = device._ctrl_params["det"]
                super().__init__(device._config, path=["dectris", "acquisition"])

            @setting_property(default=True)
            def threshold1_enabled(self):
                return self._params["thresholds"][0]["enabled"]

            @threshold1_enabled.setter
            @typecheck
            def threshold1_enabled(self, value: bool):
                self._params["thresholds"][0]["enabled"] = value

            @setting_property(default=4020.5)
            def threshold1_energy(self):
                return self._params["thresholds"][0]["energy"]

            @threshold1_energy.setter
            @typecheck
            def threshold1_energy(self, value: numbers.Real):
                self._params["thresholds"][0]["energy"] = value

            @setting_property(default=True)
            def threshold2_enabled(self):
                return self._params["thresholds"][1]["enabled"]

            @threshold2_enabled.setter
            @typecheck
            def threshold2_enabled(self, value: bool):
                self._params["thresholds"][1]["enabled"] = value

            @setting_property(default=4020.5)
            def threshold2_energy(self):
                return self._params["thresholds"][1]["energy"]

            @threshold2_energy.setter
            @typecheck
            def threshold2_energy(self, value: numbers.Real):
                self._params["thresholds"][1]["energy"] = value

            @setting_property(default=False)
            def difference_enabled(self):
                return self._params["difference"]["enabled"]

            @difference_enabled.setter
            @typecheck
            def difference_enabled(self, value: bool):
                self._params["difference"]["enabled"] = value

            @setting_property(default="full")
            def roi(self):
                return self._params["roi"]

            @roi.setter
            @typecheck
            def roi(self, value: str):
                self._params["roi"] = value

            @setting_property(default=1)
            def nb_pipeline_threads(self):
                return self._params["nb_pipeline_threads"]

            @nb_pipeline_threads.setter
            @typecheck
            def nb_pipeline_threads(self, value: numbers.Integral):
                self._params["nb_pipeline_threads"] = value

            def __info__(self):
                return "Acquisition:\n" + tabulate(self._params) + "\n\n"

        class Experiment(Settings):
            def __init__(self, device):
                self._device = device
                self._params = device._ctrl_params["exp"]
                super().__init__(device._config, path=["dectris", "experiment"])

            @setting_property(default=8041)
            def photon_energy(self):
                return self._params["photon_energy"]

            @photon_energy.setter
            @typecheck
            def photon_energy(self, value: numbers.Real):
                self._params["photon_energy"] = value

            def __info__(self):
                return "Experiment:\n" + tabulate(self._params) + "\n\n"

        class Saving(Settings):
            """ "
            {
                'enabled': False,
                'filename': {
                    'base_path': '/tmp',
                    'filename_format': '{filename_prefix}_{filename_rank}_{file_number:05d}{filename_suffix}',
                    'filename_prefix': 'lima2',
                    'filename_suffix': '.h5'
                }
            }
            """

            def __init__(self, device):
                self._device = device
                self._params = device._ctrl_params["saving"]
                super().__init__(device._config, path=["dectris", "saving"])

            @setting_property(default=False)
            def enabled(self):
                return self._params["enabled"]

            @enabled.setter
            @typecheck
            def enabled(self, value: bool):
                self._params["enabled"] = value

            @property
            def filename_prefix(self):
                return self._params["filename_prefix"]

            @filename_prefix.setter
            @typecheck
            def filename_prefix(self, value: str):
                self._params["filename_prefix"] = value

            @setting_property
            def nb_frames_per_file(self):
                return self._params["nb_frames_per_file"]

            @nb_frames_per_file.setter
            @typecheck
            def nb_frames_per_file(self, value: int):
                self._params["nb_frames_per_file"] = value

            def __info__(self):
                return "Saving:\n" + tabulate(self._params) + "\n\n"

        self.acquisition = Acquisition(device)
        self.experiment = Experiment(device)
        self.saving = Saving(device)

    def __info__(self):
        return (
            self.acquisition.__info__()
            + self.experiment.__info__()
            + self.saving.__info__()
        )

    @property
    def counters(self):
        return [
            self._temperature_cnt,
            self._humidity_cnt,
            self._raw_frame_cnt,
        ]

    @property
    def counter_groups(self):
        res = {}
        res["health"] = counter_namespace([self._temperature_cnt, self._humidity_cnt])
        res["images"] = counter_namespace([self._raw_frame_cnt])
        return res

    def __get_request_address(self, subsystem, name):
        if self._dcu_ip is None:
            raise RuntimeError("Dectris DCU IP address not configured")
        dcu = self._dcu_ip
        api = "1.8.0"
        return f"http://{dcu}/{subsystem}/api/{api}/{name}"

    def raw_command(self, subsystem, name, dict_data=None):
        address = self.__get_request_address(subsystem, name)
        if dict_data is not None:
            data_json = json.dumps(dict_data)
            request = requests.put(address, data=data_json)
        else:
            request = requests.put(address)
        if request.status_code != 200:
            raise RuntimeError(f"Command {address} failed")

    def raw_get(self, subsystem, name):
        address = self.__get_request_address(subsystem, name)
        request = requests.get(address)
        if request.status_code != 200:
            raise RuntimeError(
                f"Failed to get {address}\nStatus code = {request.status_code}"
            )
        return request.json()

    def raw_put(self, subsystem, name, dict_data):
        address = self.__get_request_address(subsystem, name)
        data_json = json.dumps(dict_data)
        request = requests.put(address, data=data_json)
        if request.status_code != 200:
            raise RuntimeError(f"Failed to put {address}")
        return request.json()

    def get(self, subsystem, name):
        raw_data = self.raw_get(subsystem, name)
        if isinstance(raw_data["value"], dict):
            return self.__raw2numpy(raw_data)
        return raw_data["value"]

    def __raw2numpy(self, raw_data):
        str_data = base64.standard_b64decode(raw_data["value"]["data"])
        data_type = DECTRIS_TO_NUMPY.get(raw_data["value"]["type"])
        arr_data = numpy.fromstring(str_data, dtype=data_type)
        arr_data.shape = tuple(raw_data["value"]["shape"])
        return arr_data

    def array2edf(self, subsystem, name, filename):
        arr_data = self.get(subsystem, name)
        if not isinstance(arr_data, numpy.ndarray):
            address = self.__get_request_address(subsystem, name)
            raise ValueError(f"{address} does not return an array !!")
        edf_file = fabio.edfimage.EdfImage(arr_data)
        edf_file.save(filename)

    def mask2lima(self, filename):
        arr_data = self.get("detector", "config/pixel_mask")
        lima_data = numpy.array(arr_data == 0, dtype=numpy.uint8)
        edf_file = fabio.edfimage.EdfImage(lima_data)
        edf_file.save(filename)

    def reset_high_voltage(self, reset_time=30.0, wait=True):
        data = {"value": float(reset_time)}
        self.raw_command("detector", "command/hv_reset", data)
        if wait:
            self.wait_high_voltage()

    def wait_high_voltage(self):
        widx = 0
        with status_message() as update:
            while True:
                gevent.sleep(0.5)
                state = self.get("detector", "status/high_voltage/state")
                if state == "READY":
                    break
                dots = "." * (widx % 4)
                update(f"High Voltage status: {state:10.10s} {dots:3.3s}")
                widx += 1
        print(f"High Voltage status: {state:20.20s}")
