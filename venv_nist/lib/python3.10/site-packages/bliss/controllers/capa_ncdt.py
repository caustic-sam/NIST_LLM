# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
BLISS controller for Micro-Epsilon CapaNCDT 6200.

This appliance uses the principle of capacitive distance measurement.
"""

import numpy
import enum
import gevent
from gevent import select
from gevent import event
import socket
import functools
import struct
import telnetlib

from bliss import global_map
from bliss.common.utils import autocomplete_property
from bliss.common.counter import SamplingCounter  # noqa: F401
from bliss.controllers.counter import SamplingCounterController
from bliss.controllers.bliss_controller import BlissController
from bliss.common.logtools import log_info


def lazy_init(func):
    @functools.wraps(func)
    def f(self, *args, **keys):
        self._start_raw_read()
        return func(self, *args, **keys)

    return f


class CapaNcdtCC(SamplingCounterController):
    def __init__(self, name, capancdt):
        self.capancdt = capancdt
        super().__init__(name)
        global_map.register(self, parent_list=["controllers", "counters"])

    def read_all(self, *counters):
        """
        Read all channels at once each time it's required.
        """
        log_info(self, "read_all")
        header, nb_channels_enable, channels_value = self.capancdt.get_data(
            wait_next=True
        )
        # channel's values arrived sorted
        channels_value = channels_value[-nb_channels_enable:]
        result = []
        sorted_counter = sorted(self.counters, key=lambda c: c.channel)
        array_index = {cnt: i for i, cnt in enumerate(sorted_counter)}
        for cnt in counters:
            index = array_index[cnt]
            result.append(channels_value[index] / 0xFFFFFF * cnt.measuring_range)

        return result


class CapaNcdtController(BlissController):
    def __init__(self, config):
        super().__init__(config)
        self._config = config
        log_info(self, f"_init__ in CapaNcdtController {config}")
        self._hardware = CapaNcdtDevice(config)
        self._scc = CapaNcdtCC(self.name, self._hardware)
        global_map.register(self, parent_list=["controllers", "counters"])

    def __info__(self, show_module_info=True):
        info_list = []
        info_list.append(f"\nGathering information from {self._config['hostname']}:\n")
        info_list.append(f"Device          : {self.hardware.device}")
        info_list.append(f"S/N             : {self.hardware.s_nb}")
        channel_status = self._hardware.channel_status
        info_list.append(f"Channel Status  : {channel_status}")
        info_list.append(f"Averaging type  : {self.hardware.averaging_type}")
        info_list.append(f"Averaging number: {self.hardware.averaging_number}")
        return "\n".join(info_list)

    def _get_subitem_default_class_name(self, cfg, parent_key):
        if parent_key == "counters":
            return "SamplingCounter"

    def _create_subitem_from_config(
        self, name, cfg, parent_key, item_class, item_obj=None
    ):
        if parent_key == "counters":
            mode = cfg.get("mode", "MEAN")
            obj = self._scc.create_counter(item_class, name, mode=mode)
            obj.channel = cfg.get("channel")
            obj.measuring_range = cfg.get("measuring_range")
            return obj

    def _load_config(self):
        for cfg in self.config["counters"]:
            self._get_subitem(cfg["name"])

    @autocomplete_property
    def counters(self):
        return self._scc.counters

    @autocomplete_property
    def hardware(self):
        return self._hardware

    @property
    def model(self):
        return self.hardware.model

    @property
    def serial_nb(self):
        return self.hardware.serial_nb

    @property
    def channel_status(self):
        return self.hardware.channel_status

    @property
    def averaging_type_available(self):
        return self.hardware.averaging_type_available

    @property
    def averaging_type(self):
        return self.hardware.averaging_type

    @averaging_type.setter
    def averaging_type(self, value):
        self.hardware.averaging_type = value

    @property
    def averaging_number(self):
        return self.hardware.averaging_number

    @averaging_number.setter
    def averaging_number(self, value):
        self.hardware.averaging_number = value


class CapaNcdtDevice:
    @enum.unique
    class Chs(enum.IntEnum):
        no_channel_available = 0
        channel_available = 1
        math_function_is_output_of_this_chanel = 2

    @enum.unique
    class Avt(enum.IntEnum):
        no_averaging = 0
        moving_average = 1
        arythmetic_average = 2
        median = 3
        dynamic_noise_rejection = 4

    def __init__(self, config):
        log_info(self, f"_init__ in CapaNcdtDevice {config}")
        self._config = config
        self._hostname = config["hostname"]
        self._port = 10001
        self._timeout = 2
        self._read_task = None
        self._last_packet = None
        self._new_data_read = event.Event()
        self._telnet = telnetlib.Telnet(host=self._hostname, port=23, timeout=1)
        self._eol = "\r\n"
        self.HALF_PACKET_SIZE = 48 / 2

        # welcome message
        self._telnet.read_until(b"\r\n", timeout=1)

        device = (self._telnet.read_until(b"\r\n", timeout=1)).decode()
        device_list = device.split(":")
        self.device = device_list[1].replace("\r\n", "")
        sn = (self._telnet.read_until(b"\r\n", timeout=1)).decode()
        sn_list = sn.split(":")
        self.s_nb = sn_list[1].replace("\r\n", "")

        for cfg in config["counters"]:
            channel = str(cfg["channel"])
            measuring_range = str(cfg["measuring_range"])
            cmd = ("$MRA" + channel + ":" + measuring_range + self._eol).encode()
            log_info(self, f"set measuring range {channel} to { measuring_range}")
            self._telnet.write(cmd)
            # flush
            self._telnet.read_until(b"\r\n")

    def __str__(self):
        # this is for the mapping: it needs a representation of instance
        return super().__repr__()

    def _start_raw_read(self):
        if not self._read_task:
            if self._read_task is not None:
                try:
                    self._read_task.get()
                finally:
                    self._read_task = None

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self._hostname, self._port))
            except Exception:
                raise ValueError("No communication channel found in config")

            self._read_task = gevent.spawn(self._raw_read, sock)

    def _raw_read(self, sock):
        try:
            while True:
                r, _, _ = select.select([sock], [], [], self._timeout)
                if r:
                    self._last_packet = sock.recv(8192)
                    if self._last_packet is not None:
                        while len(self._last_packet) <= self.HALF_PACKET_SIZE:
                            self._last_packet += sock.recv(8192)

                    self._new_data_read.set()
                else:  # someone else is connected
                    raise RuntimeError("Someone else is connected to data port")
        finally:
            self._last_packet = None
            sock.close()

    def close(self):
        if self._read_task:
            self._read_task.kill()

    @lazy_init
    def get_data(self, wait_next=False):
        while not self._last_packet or wait_next:
            self._new_data_read.clear()
            if not self._new_data_read.wait(3.0):
                if not self._read_task:
                    self._read_task.get()
                raise RuntimeError("No data arrived after 3 sec")
            else:
                break

        header_format = (
            ("Preamble", "4s"),
            ("Order_number", "i"),
            ("Serial_number", "i"),
            ("Channels", "Q"),
            ("Status", "I"),
            ("Frame_nb", "H"),
            ("Byte_per_frame", "H"),
            ("Measuring_value_counter", "i"),
        )
        struct_format = "<" + "".join(x[1] for x in header_format)
        header_size = struct.calcsize(struct_format)
        unpacked_field = struct.unpack(struct_format, self._last_packet[:header_size])
        header = {h[0]: value for h, value in zip(header_format, unpacked_field)}
        channels = header["Channels"]
        channels_enable = []
        for n in range(0, 15, 2):
            if (channels >> n) & 1 == 1:
                channels_enable.append(1)

        nb_channels_enable = len(channels_enable)
        end_size = header_size + nb_channels_enable * 4
        channels_value = numpy.fromstring(
            self._last_packet[header_size:end_size], dtype=numpy.uint32
        )
        return header, nb_channels_enable, channels_value

    @property
    def model(self):
        """Get the model number.

        Returns:
            model (str): model number
        """
        return self.device

    @property
    def serial_nb(self):
        """Get the serial number.

        Returns:
            model (int): serial number
        """
        return self.s_nb

    @property
    def channel_status(self):
        """Specifies in increasing order in which channel there is a moduel.

        Returns:
            model (str): status of the channels
        """
        cmd = ("$CHS" + self._eol).encode()
        log_info(self, f"channel_status_getter {cmd}")
        self._telnet.write(cmd)
        asw = str((self._telnet.read_until(b"\r\n")).decode())
        asw_str = asw.translate({ord(i): None for i in "$CHSOK\r\n"})
        asw_list = [int(c) for c in asw_str.split(",")]
        status_channel = [self.Chs(i).name for i in asw_list]
        return ", ".join(status_channel)

    @property
    def averaging_type_available(self):
        """List the averaging type.

        Returns:
            Averaging list (str): list
        """
        return list(map(lambda c: (c.name + ":" + str(c.value)), self.Avt))

    @property
    def averaging_type(self):
        """Get the mode of measurement averaging.

        Returns:
            Averaging type (str): serial number
        """
        cmd = ("$AVT?" + self._eol).encode()
        log_info(self, f"averaging_type_getter {cmd}")
        self._telnet.write(cmd)
        asw = str((self._telnet.read_until(b"\r\n")).decode())
        asw_nb = int(asw.translate({ord(i): None for i in "$AVTOK?\r\n"}))
        return self.Avt(asw_nb).name

    @averaging_type.setter
    def averaging_type(self, value):
        """Set the mode of measurement averaging."""
        val = str(value)
        cmd = ("$AVT" + val + self._eol).encode()
        log_info(self, f"averaging_type {cmd}")
        log_info(self, f"averaging_type_setter {cmd}")
        self._telnet.write(cmd)
        # flush
        self._telnet.read_until(b"\r\n")

    @property
    def averaging_number(self):
        """Get the number of measurement values.

        Returns:
            Number (int): number of measurement values.
        """
        cmd = ("$AVN?" + self._eol).encode()
        log_info(self, f"averaging_number_getter {cmd}")

        self._telnet.write(cmd)
        asw = str((self._telnet.read_until(b"\r\n")).decode())
        asw_nb = int(asw.translate({ord(i): None for i in "$AVNOK?\r\n"}))
        return asw_nb

    @averaging_number.setter
    def averaging_number(self, value):
        """Set  the number of measurement values."""
        val = str(value)
        cmd = ("$AVN" + val + self._eol).encode()
        log_info(self, f"averaging_number_setter {cmd}")
        self._telnet.write(cmd)
        # flush
        self._telnet.read_until(b"\r\n")
