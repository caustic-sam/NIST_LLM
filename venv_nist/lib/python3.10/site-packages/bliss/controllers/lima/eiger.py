# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import gevent
import requests
import json
import base64
import numpy
import fabio
import tabulate

from bliss.common.user_status_info import status_message
from .properties import LimaProperty
from .lima_base import CameraBase

DECTRIS_TO_NUMPY = {"<u4": numpy.uint32, "<f4": numpy.float32}


class Camera(CameraBase):
    def __init__(self, name, lima_device, proxy):
        super().__init__(name, lima_device, proxy)
        self.name = name
        self._lima_dev = lima_device
        self._proxy = proxy
        try:
            self.__hwroi_supported = self._proxy.has_hwroi_support
        except AttributeError:
            # this lima plugin device is not up to date for HW Roi support
            self.__hwroi_supported = False
        else:
            lst = self._proxy.hwroi_supported_list
            self.__hwrois = {
                lst[i]: tuple(map(lambda x: int(x), lst[i + 1 : i + 5]))
                for i in range(0, len(lst), 5)
            }
            self.__model = self._proxy.model_size

    def to_dict(self, *args, **kwargs):
        if self._proxy.api_generation.lower() == "eiger2":
            kwargs["include_properties"] = (
                # "auto_summation",
                # "countrate_correction",
                # "flatfield_correction",
                # "pixel_mask",
                # "retrigger",
                "threshold_energy",
                "threshold_energy2",
                "threshold_diff_mode",
                # "virtual_pixel_correction",
            )
        else:
            kwargs["include_properties"] = (
                # "auto_summation",
                # "countrate_correction",
                # "efficiency_correction",
                # "flatfield_correction",
                # "pixel_mask",
                "threshold_energy",
                # "virtual_pixel_correction",
            )
        return super().to_dict(*args, **kwargs)

    @property
    def synchro_mode(self):
        return "IMAGE"

    @LimaProperty
    def stream_stats(self):
        stats_val = self._proxy.stream_stats
        stats_txt = "{0:d} frames, {1:.3f} MB, {2:.3f} ms, {3:.3f} GB/s".format(
            int(stats_val[0]),
            stats_val[1] / (1024.0 * 1024.0),
            stats_val[2] / 1.0e-3,
            stats_val[3] / (1024.0 * 1024.0 * 1024.0),
        )
        return stats_txt

    def initialize(self, wait=True):
        self._proxy.initialize()
        if wait:
            self.wait_initialize()

    def wait_initialize(self):
        widx = 0
        with status_message() as update:
            while True:
                gevent.sleep(0.5)
                status = self._proxy.plugin_status
                if status in ["READY", "FAULT"]:
                    break
                dots = "." * (widx % 4)
                update(f"Detector status: {status:15.15s} {dots:3.3s}")
                widx += 1
        print(f"Detector status: {status:20.20s}")
        self.wait_high_voltage()

    def delete_memory_files(self):
        self._proxy.deleteMemoryFiles()

    def reset_high_voltage(self, reset_time=30.0, wait=True):
        # self._proxy.resetHighVoltage()
        data = {"value": float(reset_time)}
        self.raw_command("detector", "command/hv_reset", data)
        if wait:
            self.wait_high_voltage()

    def wait_high_voltage(self):
        widx = 0
        with status_message() as update:
            while True:
                gevent.sleep(0.5)
                state = self._proxy.high_voltage_state
                if state == "READY":
                    break
                dots = "." * (widx % 4)
                update(f"High Voltage status: {state:10.10s} {dots:3.3s}")
                widx += 1
        print(f"High Voltage status: {state:20.20s}")

    def __info__(self):
        status = [
            "temperature",
            "humidity",
            "high_voltage_state",
            "plugin_status",
            "cam_status",
            "serie_id",
            "stream_stats",
            "stream_last_info",
        ]
        info = f"{self._lima_dev.proxy.camera_model} - ({self._lima_dev.proxy.camera_type})\n\n"
        info += self.__get_info_txt("Detector Status", status)
        config = [
            "countrate_correction",
            "flatfield_correction",
            "auto_summation",
            "virtual_pixel_correction",
            "threshold_diff_mode",
            "retrigger",
            "pixel_mask",
            "compression_type",
        ]
        if self._proxy.api_generation.lower() == "eiger1":
            config.remove("retrigger")
            config.remove("threshold_diff_mode")
            config.append("efficiency_correction")

        info += self.__get_info_txt("Configuration", config)
        calibration = ["photon_energy", "threshold_energy", "threshold_energy2"]
        if self._proxy.api_generation.lower() == "eiger1":
            calibration.remove("threshold_energy2")
        info += self.__get_info_txt("Calibration", calibration)

        if self.__hwroi_supported:
            info += self.__hwroi_list()

        return info

    def __get_info_txt(self, title, attr_list):
        info = f"{title}:\n"
        hlen = 1 + max([len(attr) for attr in attr_list])
        for name in attr_list:
            try:
                value = getattr(self, name)
            except AttributeError:
                value = "!ERROR!"
            prop = getattr(self.__class__, name)
            flag = prop.fset and "RW" or "RO"
            info += f"    {name:{hlen}s}: {repr(value)}  [{flag}]\n"
        return info

    def __get_request_address(self, subsystem, name):
        dcu = self._proxy.detector_ip
        api = self._proxy.api_version
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

    def hwroi_list(self):
        print(self.__hwroi_list())

    def __hwroi_list(self):
        hwrois = self.__hwrois
        current = self.hwroi_get()
        tab_data = list()
        for (name, values) in hwrois.items():
            state = current == name and ">>>" or ""
            tab_data.append([state, name] + list(values))
        tab_head = ["set", "name", "x0", "y0", "width", "height"]
        info = "\nHardware ROI values for [{0}]:\n".format(self.__model)
        info += tabulate.tabulate(tab_data, tab_head) + "\n"
        return info

    def hwroi_set(self, name):
        if not self.__hwroi_supported:
            print("HW ROI is not supported by this model!!")
            return

        hwrois = self.__hwrois
        if name is None:
            name = "disabled"
        elif name not in hwrois.keys():
            raise RuntimeError("Unknown HWROI for model [{0}]".format(self._model))
        roi = hwrois[name]
        print(
            "\tSetting Image ROI to ({0},{1},{2},{3}) to get HW ROI {4}".format(
                *roi, name
            )
        )
        self._lima_dev.image.roi = roi[:4]

    def hwroi_get(self):
        if not self.__hwroi_supported:
            return "HW ROI is not supported by this model!!"

        hwrois = self.__hwrois
        img_roi = tuple(self._lima_dev.image.roi)
        for name in hwrois.keys():
            if hwrois[name] == img_roi:
                return name
        return "SOFT"

    def hwroi_reset(self):
        self.hwroi_set(None)

    @property
    def hwroi(self):
        return self.hwroi_get()

    @hwroi.setter
    def hwroi(self, name):
        self.hwroi_set(name)
