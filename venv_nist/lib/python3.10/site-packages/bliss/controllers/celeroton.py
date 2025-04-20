# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
The Celeroton Fast Chopper is using serial line binary protocol.
A command always consist of a request package and a reply package,
containing at least an acknowledgment or an error code. The format is:
Request:

.. code-block::

             |-------------------- length ---------------------|
    ------------------------------------------------------------
    | length | command | data[0] | ... | data [n-1] | checksum |
    ------------------------------------------------------------
    |------------------ checksum -------------------|

Reply:

.. code-block::

    -------------------------------------------------------
    | length | ~command | LSB Error | MSB error| checksum |
    -------------------------------------------------------

Each block is 1 byte. The length includes all the blocks, except the length one.
The checksum is calculated on all the blocks, using the formula

.. code-block:: python

    checksum = (~(length+command+sum(data[0]...data[n-1]))+1) & 0xff

The serial line configuration is 57600 baud rate, 1 stop bit and 8 data bits.
Use the struct python module to interpret bytes as binary data.

The data, status and the error codes are transmitted LSB first

Example yml configuration example:

.. code-block::

    name: fast_chopper
    class: Celeroton
    serial:
       url: "rfc2217://lid293:28010"       #serial line name
    or
       url: "tango://id29:20000/id29/chopper/fast"
    or
    tcp:
        url: id29serial1.esrf.fr:9001
"""
import struct
from gevent import sleep
from bliss.comm.util import get_comm, get_comm_type
from bliss.comm.exceptions import CommunicationTimeout


ERROR_CODE = {
    0x4001: "Unknown command",
    0x4002: "Wrong checksum",
    0x4004: "Invalid format",
    0x4008: "Read only",
    0x4010: "Type mismatch",
    0x4020: "Unknown variable",
    0x4040: "Save is not possible",
}

VARIABLE_CODE = {
    "Reference speed": 0x00,
    "Actual speed": 0x01,
    "DC-Link current": 0x02,
    "DC-Link current reference": 0x03,
    "Converter temperature": 0x04,
    "DC-Link voltage": 0x05,
    "Output power": 0x06,
    "Motor temperature (THC)": 0x07,
    "Motor temperature (PTC)": 0x08,
    "Pole pairs": 0x09,
    "Max. phase current": 0x0A,
    "Max. rotational speed": 0x0B,
    "Synchronization current": 0x0C,
    "Axial moment of inertia": 0x0D,
    "PM flux linkage": 0x0E,
    "Phase inductance": 0x0F,
    "Phase resistance": 0x10,
    "Rotation direction": 0x11,
    "Acc. Ratio (above sync.)": 0x12,
    "Acc. Ratio (below sync.)": 0x13,
    "Speed controller rise time": 0x14,
    "User defined sync. Speed": 0x15,
    "Default sync speed (lower)": 0x16,
    "Default sync speed (upper)": 0x17,
    "User def. sync speed (lower)": 0x18,
    "User def. sync speed (upper)": 0x19,
    "User defined control parameter": 0x1A,
    "Proportional speed gain": 0x1B,
    "Integral speed gain": 0x1C,
}

VARIABLE_TYPE = {
    "Int16": 0x01,
    "Uint16": 0x02,
    "Int32": 0x03,
    "Uint32": 0x04,
    "Float": 0x05,
}


class Celeroton:
    """Commands"""

    def __init__(self, config):
        self.debug = False

        comm_type = get_comm_type(config)
        self._comm = get_comm(config, timeout=2)
        if comm_type == "serial":
            self._comm._serial_kwargs["baudrate"] = 57600

        self._comm.flush()
        # synchronise with the controller
        self.flush()

    def __info__(self):
        """Return some useful information
        Returns:
            (str): The status as string
        """
        info_str = ""
        info_str += f"  Reference speed: {self.reference_speed} rpm\n"
        info_str += f"     Actual speed: {self.actual_speed} rpm\n"
        info_str += f"Motor temperature: {self.temperature} °C\n"
        return info_str

    def flush(self):
        """Transmit a sequence of 16 zeros after every communication error to
        ensures that the reception of any partly received package is completed.
        Subsequently, the state machine is guaranteed to be in idle state and
        synchronized to the master.
        """
        # send 16 zero bytes + checksum = 0 byte
        self._comm.write(b"\x00" * 17)
        sleep(1)
        self._clean()

    def _clean(self):
        """Clean the buffer"""
        try:
            print(f"{len(self._comm.raw_read())}")
        except CommunicationTimeout:
            pass

    def start(self):
        """Start the motor with the currently set speed reference.
        Raises:
            RuntimeError: Error reported by the controller.
        """
        request = b"\x02\x02\xfc"
        with self._comm._lock:
            reply = self._comm.write_read(request, size=1)
            if reply == b"\x02":
                reply += self._comm.read(2)
            elif reply == b"\x12":
                reply += self._comm.read(18)
                err = self._check_error(reply)
            else:
                err = f"incorrect reply {reply}"
                self.flush()
        if self.debug:
            print(f"reply {reply}")
        if request != reply:
            err_str = f"Start not executed, {err}"
            raise RuntimeError(err_str)

    def stop(self):
        """Stop the motor.
        Raises:
            RuntimeError: Error reported by the controller.
        """
        request = b"\x02\x03\xfb"
        with self._comm._lock:
            reply = self._comm.write_read(request, size=1)
            if reply == b"\x02":
                reply += self._comm.read(2)
            elif reply == b"\x12":
                reply += self._comm.read(18)
                err = self._check_error(reply)
            else:
                self.flush()
                err = f"incorrect reply {reply}"
        if self.debug:
            print(f"reply {reply}")
        if request != reply:
            err_str = f"Stop not executed, {err}"
            raise RuntimeError(err_str)

    def ack_error(self, error=None):
        """Acknowledge errors and warnings, which prevent the starting of
        the motor.
        Args:
            error(str): Byte strig (8 bytes), coming from the status reading.
        """
        if error:
            request = b"\x0a\x01" + error
        else:
            err1, err2, _, _ = self.status()
            request = b"\x0a\x01" + struct.pack("<II", err1, err2)

        checksum = self._calc_checksum(request)
        request += struct.pack("<B", checksum)

        with self._comm._lock:
            reply = self._comm.write_read(request, size=1)
            if reply == b"\x02":
                reply += self._comm.read(2)
            if reply != b"\x02\x01\xfd":
                err_str = f"Error not acknowledged, invalid reply {reply}"

                self.flush()
                raise RuntimeError(err_str)

    def status(self):
        """Read the controller internal status.
        Returns:
            (int): status code, if any. 0 otherwise
        """
        request = b"\x02\x00\xfe"
        if self.debug:
            print(f"request {request}")
        with self._comm._lock:
            reply = self._comm.write_read(request, size=1)
            if reply[0] == 0x12:
                reply += self._comm.read(18)
            else:
                self.flush()
                raise RuntimeError("Incorrect status reply")
        if self.debug:
            print(f"reply {reply}")
        try:
            return struct.unpack("<IIII", reply[2:18])
        except struct.error as exc:
            raise RuntimeError("Ivalid status reply") from exc

    @property
    def reference_speed(self):
        """Read the reference speed.
        Returns:
            (int): The reference speed [rpm]
        """
        speed = self._read_value(VARIABLE_CODE["Reference speed"])
        return speed

    @reference_speed.setter
    def reference_speed(self, value):
        """Set the actual speed.
        Args:
            value (int): The actual speed [rpm]
        """
        self._write_value(
            VARIABLE_CODE["Reference speed"], value, VARIABLE_TYPE["Int32"]
        )

    @property
    def temperature(self):
        """Read the temperature
        Returns:
            (int): Temperature [°C]
        """
        # return self._read_value(VARIABLE_CODE["Converter temperature"])
        return self._read_value(VARIABLE_CODE["Motor temperature (THC)"])

    @property
    def actual_speed(self):
        """Read the actual speed.
        Returns:
            (int): The actual speed [rpm]
        """
        speed = self._read_value(VARIABLE_CODE["Actual speed"])
        return speed

    def _read_value(self, code):
        """Read the value of a variable specified by its code.
        Args:
            code (int): Variable byte code.
        Returns:
            (int) or (float): Actual value.
        """
        err = False
        req_length = 0x03
        reply_length = 0x07
        cmd = 0x04
        checksum = self._calc_checksum(code, req_length, cmd)

        request = struct.pack("<BBBB", req_length, cmd, code, checksum)
        if self.debug:
            print(f"request {request}")
        with self._comm._lock:
            reply = self._comm.write_read(request, size=1)
            if reply[0] == reply_length:
                reply += self._comm.read(7)
                if reply[1] != cmd:
                    err = f"incorrect reply {reply}"
            elif reply == b"\x12":
                reply += self._comm.read(18)
                err = self._check_error(reply)
            else:
                self.flush()
                err = f"ivalid reply {reply}"

        if self.debug:
            print(f"reply {reply}")
        if err:
            raise RuntimeError(err)

        # get the value depending on the variable type
        conv_format = self._read_conversion_format(reply[2])
        return struct.unpack(conv_format, reply)[3]

    def _write_value(self, code, value, value_type):
        """Write value to a variable specified by its code.
        Args:
            code (int): Variable byte code.
            value (int) or (float): Value to be set.
            value_type (int): Value type as in VARIABLE_TYPE.
        """
        err = False
        req_length = 0x08
        reply_length = 0x02
        cmd = 0x05
        reply_checksum = 0xF9
        conv_format = self._write_conversion_format(value_type)

        request = struct.pack(conv_format, *(req_length, cmd, code, value_type, value))
        checksum = self._calc_checksum((request))
        request += struct.pack("<B", checksum)
        if self.debug:
            print(f"request {request}")
        with self._comm.lock:
            reply = self._comm.write_read(request, size=1)
            if reply[0] == reply_length:
                reply += self._comm.read(2)
                if reply[1] != cmd or reply[2] != reply_checksum:
                    err = f"Incorrect reply {reply}"
            elif reply == b"\x12":
                reply += self._comm.read(18)
                err = self._check_error(reply)
            else:
                self.flush()
                err = f"Ivalid reply {reply}"

        if self.debug:
            print(f"reply {reply}")
        if err:
            raise RuntimeError(err)

    def _calc_checksum(self, code, length=None, cmd=None):
        """Calculate the checksum for a given variable.
        Args:
            code (int): Variable byte code.
            length (int): Command length
            cmd (int): Command byte code
        Returns:
            (int): Calculated checksum [bytes].
        """
        if isinstance(code, bytes):
            _sum = ~sum(code)
        else:
            _sum = ~sum((length, cmd, code))
        return (_sum + 1) & 0xFF

    def _check_error(self, error):
        """Conver the error from the controller to be human readble.
        Args:
            error (byte string): The raw error
        Returns:
            (str): Error string.
        """
        err = struct.unpack("<BBHHB", error)

        # Conver to human readable message
        try:
            return ERROR_CODE[err[2]]
        except ValueError:
            return "Unknown error"

    def _read_conversion_format(self, var_type):
        """Choose the conversion format.
        Args:
            vat_type (int): Variable type. Accepted values as in VARIABLE_TYPE
        Returns:
            (str): String to be used as format by struct.unpack.
        Raises:
            RuntimeError: Unknown variable type.
        """

        if var_type in (VARIABLE_TYPE["Int16"], VARIABLE_TYPE["Int32"]):
            return "<BBBiB"
        if var_type in (VARIABLE_TYPE["Uint16"], VARIABLE_TYPE["Uint32"]):
            return "<BBBIB"
        if var_type == VARIABLE_TYPE["Float"]:
            return "<BBBfB"
        raise RuntimeError("Unknown variable type")

    def _write_conversion_format(self, var_type):
        """Choose the conversion format.
        Args:
            vat_type (int): Variable type. Accepted values as in VARIABLE_TYPE
        Returns:
            (str): String to be used as format by struct.pack.
        Raises:
            RuntimeError: Unknown variable type.
        """

        if var_type == VARIABLE_TYPE["Int16"]:
            return "<BBBBh"
        if var_type == VARIABLE_TYPE["Uint16"]:
            return "<BBBBH"
        if var_type == VARIABLE_TYPE["Int32"]:
            return "<BBBBi"
        if var_type == VARIABLE_TYPE["Uint32"]:
            return "<BBBBI"
        if var_type == VARIABLE_TYPE["Float"]:
            return "<BBBBf"
        raise RuntimeError("Unknown variable type")
