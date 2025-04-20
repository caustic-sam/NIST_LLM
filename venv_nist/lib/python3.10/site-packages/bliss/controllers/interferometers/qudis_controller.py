# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
quDIS interferometer
"""

import ctypes
import platform
import sys

# Callback function
CALLBACK = ctypes.CFUNCTYPE(
    None,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.POINTER(ctypes.POINTER(ctypes.c_double)),
    ctypes.POINTER(ctypes.c_int32),
)


@CALLBACK
def rel_position_callback(dev, count, index, pos, marker):
    # index + count = index of next call
    for i in range(count):
        print(f"relative: {pos[0][i]} {pos[1][i]} {pos[2][i]}")


@CALLBACK
def abs_position_callback(dev, count, index, pos, marker):
    # index + count = index of next call
    for i in range(count):
        print(f"absolute: {pos[0][i]} {pos[1][i]} {pos[2][i]}")


class QudisController:
    """
    QudisController
    """

    def __init__(self, name, config):

        self.config = config
        self.name = name
        # load Lib -------------------------------------------
        if platform.system() == "Windows":
            self.qdslib = ctypes.windll.LoadLibrary("qudis.dll")
        if platform.system() == "Linux":
            self.qdslib = ctypes.cdll.LoadLibrary(
                "/users/blissadm/local/bliss.git/bliss/controllers/interferometers/libqudis.so"
            )

        # ------- tdcbase.h --------------------------------------------------------
        self.qdslib.QDS_discover.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint),
        ]
        self.qdslib.QDS_discover.restype = ctypes.c_int32

        self.qdslib.QDS_getDeviceInfo.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_char),
            ctypes.POINTER(ctypes.c_char),
        ]
        self.qdslib.QDS_getDeviceInfo.restype = ctypes.c_int32

        self.qdslib.QDS_connect.argtypes = [ctypes.c_uint]
        self.qdslib.QDS_connect.restype = ctypes.c_int32

        self.qdslib.QDS_disconnect.argtypes = [ctypes.c_uint]
        self.qdslib.QDS_disconnect.restype = ctypes.c_int32

        self.qdslib.QDS_setPositionCallback.argtypes = [
            ctypes.c_uint,
            CALLBACK,
            CALLBACK,
        ]
        self.qdslib.QDS_setPositionCallback.restype = ctypes.c_int

        self.qdslib.QDS_getPositions.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
        ]
        self.qdslib.QDS_getPositions.restype = ctypes.c_int

        dev_count = ctypes.c_uint()

        if_ethernet = 0x02
        if_usb3 = 0x10
        if_all = if_ethernet + if_usb3
        dev_count = ctypes.c_uint()
        rc = self.qdslib.QDS_discover(
            if_all, ctypes.byref(dev_count)
        )  # accept any device
        if self.perror("QDS_discover", rc):
            sys.exit(1)
        if dev_count.value > 0:
            self.qdslib.QDS_connect(0)

    @property
    def poll_position(self, dev=0):
        for i in range(120):
            rel_val = (ctypes.c_double * 3)()
            abs_val = (ctypes.c_double * 3)()
            self.qdslib.QDS_getPositions(0, rel_val, abs_val)
            print(f"relative: {rel_val[0]} {rel_val[1]} {rel_val[2]}")
            print(f"absolute: {abs_val[0]} {abs_val[1]} {abs_val[2]}")

    @property
    def stream_position(qudis, dev=0):
        return qudis.qdslib.QDS_setPositionCallback(
            dev, rel_position_callback, abs_position_callback
        )

    def __info__(self):
        device_id = ctypes.c_int()
        serial_no = (ctypes.c_char * 20)()
        ipaddr = (ctypes.c_char * 20)()
        self.qdslib.QDS_getDeviceInfo(0, ctypes.byref(device_id), serial_no, ipaddr)
        info_str = f"Device name: {self.name}"
        info_str += "\nS/N: "
        info_str += "".join(map(chr, serial_no.value))
        info_str += "\nIP address: "
        info_str += "".join(map(chr, ipaddr.value))
        return info_str

    def __err_msg(self, code):
        if code == 0:
            return "Success"
        if code == 1:
            return "Receive timed out"
        if code == 2:
            return "No connection was established"
        if code == 3:
            return "Error accessing the USB driver"
        if code == 7:
            return "Can't connect device because already in use"
        if code == 8:
            return "Unknown error"
        if code == 9:
            return "Invalid device number used in call"
        if code == 10:
            return "Parameter in function call is out of range"
        if code == 11:
            return "Failed to open specified file"
        if code == 12:
            return "Library has not been initialized"
        if code == 13:
            return "Requested feature is not enabled"
        if code == 14:
            return "Requested feature is not available"

        return "Unspecified error"

    def perror(self, environ, return_code):
        if return_code != 0:
            msg = self.qdslib.__err_msg(return_code)
            print(f"Error in {environ}: {msg}")
            return True
        return False
