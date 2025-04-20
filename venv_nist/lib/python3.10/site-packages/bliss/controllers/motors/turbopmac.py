# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

# documentation : Turbo PMAC User Manual.
# Chapter : Turbo PMAC Ethernet Protocol, p. 412


import re
import enum
import socket
import struct
from collections import OrderedDict

import numpy as np
import gevent

from bliss import global_map
from bliss.common.axis.axis import Axis
from bliss.common.logtools import log_debug, log_error, log_info
from bliss.comm.util import get_comm
from bliss.common.axis.state import AxisState
from bliss.controllers.motor import Controller


# flush timeout in ms
_PMAC_FLUSH_TIMEOUT = 62465
# 500


# pmac default port if not set in the config
_PMAC_DEFAULT_PORT = 1025


# max size of PMAC's buffer
_PMAC_MAX_BUFFER_SIZE = 1400


# motor status to bliss axis state
# tuple: status bit, bliss state if bit set, bliss state if bit unset, info
#
# !!!!
# Some more state translation is done
# in the TurboPmac._pmac_state method
# in which READY takes precedence over MOVING
# !!!!
#
# amplifier enabled = 0 : not moving
# in position = 1: not moving
class PMAC_MOTOR_STATUS(enum.Enum):
    # first 6 characters (bits 47 to 24)
    # ((1 << 23 + 24), "ON")
    MOT_ACTIVATED = (1 << (23 + 24), None, "OFF", "Motor activated")
    LIMNEG = (1 << (22 + 24), "LIMNEG", None, "Negative end limit set")
    LIMPOS = (1 << (21 + 24), "LIMPOS", None, "Positive end limit set")
    SERVO_ENAB = (1 << (20 + 24), None, None, "Extended servo algorithm enabled")
    AMP_ENAB = (1 << (19 + 24), None, "READY", "Amplifier Enabled")
    OPEN_LOOP = (1 << (18 + 24), "READY", None, "Open Loop Mode")
    MOVE_TIMER = (1 << (17 + 24), "MOVING", "READY", "Move Timer Active")
    INTEG_MODE = (1 << (16 + 24), None, None, "Integration Mode")
    DWELL = (1 << (15 + 24), None, None, "Dwell in Progress")
    DATA_BLK_ERR = (1 << (14 + 24), None, None, "Data Block Error")
    VEL_ZERO = (1 << (13 + 24), None, "MOVING", "Desired Velocity Zero")
    ABORT_DECEL = (1 << (12 + 24), None, None, "Abort Deceleration")
    BLOCK_REQUEST = (1 << (11 + 24), None, None, "Block Request:")
    HOME_SEARCH = (1 << (10 + 24), "HOMING", None, "Home Search in Progress")
    USER_PHASE = (1 << (9 + 24), None, None, "User-Written Phase Enable")
    USER_SERVO = (1 << (8 + 24), None, None, "User-Written Servo Enable")
    ALT_SRC_DEST = (1 << (7 + 24), None, None, "Alternate Source/Destination")
    PHASED_MOT = (1 << (6 + 24), None, None, "Phased Motor")
    FOL_OFST = (1 << (5 + 24), None, None, "Following Offset Mode")
    FOL_ENAB = (1 << (4 + 24), None, None, "Following Enabled")
    ERR_TRIG = (1 << (3 + 24), None, None, "Error Trigger")
    SOFT_POS_CAPT = (1 << (2 + 24), None, None, "Software Position Capture")
    ALT_CMD_OUT = (1 << (1 + 24), None, None, "Alternate Command-Output Mode")
    MAX_RAPID_SPD = (1 << (0 + 24), None, None, "Maximum Rapid Speed")
    # last 6 charaters (bits 23 to 0)
    CS_BIT3 = (1 << 23, None, None, "Coordinate system bit3")
    CS_BIT2 = (1 << 22, None, None, "Coordinate system bit2")
    CS_BIT1 = (1 << 21, None, None, "Coordinate system bit1")
    CS_BIT0 = (1 << 20, None, None, "Coordinate system bit0")
    CS_DEF_BIT3 = (1 << 19, None, None, "Coordinate definition bit3 ")
    CS_DEF_BIT2 = (1 << 18, None, None, "Coordinate definition bit2")
    CS_DEF_BIT1 = (1 << 17, None, None, "Coordinate definition bit1")
    CS_DEF_BIT0 = (1 << 16, None, None, "Coordinate definition bit0")
    CS_ASSIGNED = (1 << 15, None, None, "Assigned to coordinate system")
    RFU = (1 << 14, None, None, "RFU")
    FG_IN_POS = (1 << 13, None, None, "Foreground In-Position")
    STOP_DESIRED_POS_LIM = (1 << 12, None, None, "Stopped on Desired Position Limit")
    STOP_POS_LIM = (1 << 11, None, None, "Stopped on Position Limit")
    HOMED = (1 << 10, "HOMED", None, "Home Complete")
    PHAS_SRCH_READ = (1 << 9, None, None, "Phasing Search/Read Active")
    PHAS_REF_ERR = (1 << 8, "FAULT", None, "Phasing Reference Error")
    TRIG_MOVE = (1 << 7, None, None, "Trigger Move")
    INTEG_FATAL_FOL_ERR = (1 << 6, "FAULT", None, "Integrated Fatal Following Error")
    I2T_AMPL_FAULT_ERR = (1 << 5, "FAULT", None, "I2T Amplifier Fault Error")
    BKLASH_DIR_FLAG = (1 << 4, None, None, "Backlash Direction Flag")
    AMPLI_FAULT = (1 << 3, "FAULT", None, "Amplifier Fault")
    FATAL_FOL_ERR = (1 << 2, "FAULT", None, "Fatal Following Error")
    WARN_FOL_ERR = (1 << 1, "WARN", None, "Warning Following Error")
    # only set when the loop is closed
    # + some other conditions, see manual
    IN_POSITION = (1 << 0, "READY", None, "In Position")
    # (1 << 0, "READY", "MOVING", "In Position"),


PMAC_ERR_CODES = {
    "ERR001": (
        "Command not allowed during program execution",
        "should halt program execution before issuing command",
    ),
    "ERR002": ("Password error", "should enter the proper password"),
    "ERR003": (
        "Data error or unrecognized command",
        "should correct syntax of command",
    ),
    "ERR004": (
        "Illegal character: bad value (>127 ASCII) or serial parity/framing error",
        "should correct the character and or check for noise on the serial cable",
    ),
    "ERR005": (
        "Command not allowed unless buffer is open",
        "should open a buffer first",
    ),
    "ERR006": (
        "No room in buffer for command",
        "shouldd allow more room for buffer -- DELETE or CLEAR other buffers",
    ),
    "ERR007": ("Buffer already in use", "shouldd CLOSE currently open buffer first"),
    "ERR008": (
        "MACRO auxiliary communications error",
        "shouldd check MACRO ring hardware and software setup",
    ),
    "ERR009": (
        "Program structural error (e.g. ENDIF without IF)",
        "should correct structure of program",
    ),
    "ERR010": (
        "Both overtravel limits set for a motor in the C. S.",
        "should correct or disable limits",
    ),
    "ERR011": (
        "Previous move not completed",
        "shouldd Abort it or allow it to complete",
    ),
    "ERR012": (
        "A motor in the coordinate system is open-loop",
        "shouldd close the loop on the motor",
    ),
    "ERR013": (
        "A motor in the coordinate system is not activated",
        "shouldd set Ix00 to 1 or remove motor from C.S.",
    ),
    "ERR014": (
        "No motors in the coordinate system",
        "shouldd define at least one motor in C.S.",
    ),
    "ERR015": (
        "Not pointing to valid program buffer",
        "shouldd use B command first, or clear out scrambled buffers",
    ),
    "ERR016": (
        "Running improperly structured program (e.g. missing ENDWHILE)",
        "should correct structure of program",
    ),
    "ERR017": (
        "Trying to resume after H or Q with motors out of stopped position",
        "shouldd use J= to return motor[s] to stopped position",
    ),
    "ERR018": (
        "Attempt to perform phase reference during move, move during phase reference., or enabling with",
        "shouldd finish move before phase reference, finish phase reference before move, or fix phase clock source problem phase clock error.",
    ),
    "ERR019": (
        "Illegal position-change command while moves stored in CCBUFFER",
        "shouldd pass through section of Program requiring storage of moves in CCBUFFER, or abort",
    ),
}


def pmac_raw_write_read(pmac, com):
    """
    Sends a command to the pmac and converts the reply to a list
    in the case of multiple replies else a single value.
    """
    reply = pmac.raw_write_read(com).split("\r")
    if len(reply) == 1:
        return reply[0]
    return reply


def pmac_axis_is_open_loop(axis):
    """
    Sends a command to the pmac and converts the reply to a list
    in the case of multiple replies else a single value.
    """
    axis_address = axis.config.get("pmac_address")
    status = int(pmac_raw_write_read(axis.controller, f"#{axis_address}?"), base=16)
    return (status & PMAC_MOTOR_STATUS.OPEN_LOOP.value[0]) != 0


def pmac_axis_kill(axis):
    axis_address = axis.config.get("pmac_address")
    pmac_raw_write_read(axis.controller, f"#{axis_address}K")


def pmac_axis_close_loop(axis):
    axis_address = axis.config.get("pmac_address")
    pmac_raw_write_read(axis.controller, f"#{axis_address}J/")


def pmac_error_code_info(code):
    return PMAC_ERR_CODES.get(code, ("UNKNOWN", "UKNOWN"))


class InvalidAddressException(Exception):
    pass


def pmac_motor_status_to_str(state):
    text = "\n".join(
        [
            f"{i//24}-{23-i%24}: {v.value[3]}"
            for i, v in enumerate(PMAC_MOTOR_STATUS)
            if v.value[0] & state
        ]
    )
    return hex(state) + "\n" + text


class PmacRequestType(enum.IntEnum):
    VR_DOWNLOAD = 0x40
    VR_UPLOAD = 0xC0


class PmacRequest(enum.IntEnum):
    VR_PMAC_SENDLINE = 0xB0
    VR_PMAC_GETLINE = 0xB1
    VR_PMAC_FLUSH = 0xB3
    VR_PMAC_GETMEM = 0xB4
    VR_PMAC_SETMEM = 0xB5
    VR_PMAC_SETBIT = 0xBA
    VR_PMAC_SETBITS = 0xBB
    VR_PMAC_PORT = 0xBE
    VR_PMAC_GETRESPONSE = 0xBF
    VR_PMAC_READREADY = 0xC2
    VR_CTRL_RESPONSE = 0xC4
    VR_PMAC_GETBUFFER = 0xC5
    VR_PMAC_WRITEBUFFER = 0xC6
    VR_PMAC_WRITEERROR = 0xC7
    VR_FWDOWNLOAD = 0xCB
    VR_IPADDRESS = 0xE0


class TurboPmacCommand:
    request_type = None
    request = None
    expected_reply = None
    flush = False
    eol = None

    def __init__(
        self, comm, data=None, value=None, index=None, data_len=None, **kwargs
    ):
        assert self.request_type in PmacRequestType
        assert self.request in PmacRequest

        self._comm = comm
        self._reply_data = b""
        self._done = False
        self._is_error = False
        self._error_code = None

        if data:
            if not isinstance(data, (bytes,)):
                data = f"{data}".encode()
            data_len = len(data)
        else:
            data = ""
            if data_len is None or data_len < 0:
                data_len = 0

        self._data = data
        self._value = value if value else 0
        self._index = index if index else 0
        self._command = struct.pack(
            "BBHHH",
            self.request_type,
            self.request,
            self._value,
            self._index,
            socket.htons(data_len),
        )
        if data:
            self._command += data

    def command(self):
        return self._command

    def _set_is_error(self):
        self._is_error = True

    def is_error(self):
        return self._is_error

    def _set_error_code(self, code):
        self._error_code = code

    def error_code(self):
        return self._error_code

    def reply(self):
        if not self._done:
            raise RuntimeError("Command not complete.")
        return self._reply_data

    def _send(self, flush=None):
        if self.flush and flush is not False:
            flush_pmac_comm(self._comm)
        command_raw = self.command()
        self._comm.write(command_raw)

    def _read(self, eol=None):
        if eol is not None:
            return self._comm.readline(eol=eol)
        return self._comm.raw_read(maxsize=_PMAC_MAX_BUFFER_SIZE)

    def process(self):
        self._send()
        data = self._read(eol=self.eol)
        if data != self.expected_reply:
            self._set_is_error()
            raise RuntimeError(
                f"Unexpected reply to command {self.command()} :"
                f" {data}. (expected {self.expected_reply}."
            )
        return data


class PmacFlush(TurboPmacCommand):
    request_type = PmacRequestType.VR_DOWNLOAD
    request = PmacRequest.VR_PMAC_FLUSH
    expected_reply = b"@"


def flush_pmac_comm(comm):
    command = PmacFlush(comm)
    command.process()


class PmacSendline(TurboPmacCommand):
    request_type = PmacRequestType.VR_DOWNLOAD
    request = PmacRequest.VR_PMAC_SENDLINE
    expected_reply = b"@"
    flush = True


class PmacGetBuffer(TurboPmacCommand):
    request_type = PmacRequestType.VR_UPLOAD
    request = PmacRequest.VR_PMAC_GETBUFFER

    def process(self):
        self._send()
        ack = False
        bell = False
        empty = False

        # TODO : proof read
        lines = []

        data = b""
        # TODO: it seems that the PMAC returns \x00 as first char
        # when the buffer is empy
        while not ack and not bell:
            # vr_pmac_getbuffer returns:
            # - anything up to an ACK or "\n"
            # OR
            # - the \r following a BELL (so we get \x07ERRnnn\r)
            data += self._read()
            new_lines = [line for line in data.split(b"\r") if line]
            if new_lines[-1] == b"\x06":
                # last char is ACK
                ack = True
                lines += new_lines[:-1]
            elif new_lines[-1].startswith(b"\x07"):
                # last received data is <BELL> + ERRnnn
                bell = True
                lines += new_lines
            elif new_lines[0] == b"\x00":
                pmac_data = []
                empty = True
                break
            else:
                # if data has more than the max buffer size: keep reading
                if len(data) == _PMAC_MAX_BUFFER_SIZE:
                    if data[-1] in (b"\r", b"\n"):
                        lines += new_lines
                        data = b""
                    else:
                        lines += new_lines[:-1]
                        data = new_lines[-1]
                    # we haven't received everything
                    # sending the getbuffer again
                    self._send()
                else:
                    data = b""
                    lines += new_lines
        if ack:
            # Expecting the <ACK> to be the last char
            pmac_data = [line.decode() for line in lines]

        elif bell:
            # received a BELL char -> ERROR
            error = lines[-1].lstrip(b"\x07").decode()
            if not error.startswith("ERR"):
                msg = (
                    f"Received a <BELL> char, but unexpected error msg format: {error}.\n"
                    f"(Full data: {lines})"
                )
                log_error(self, msg)
                raise RuntimeError(msg)
            self._set_is_error()
            self._set_error_code(error)
            error_info = pmac_error_code_info(error)
            msg = f"PMAC replied with an error code: {error} ({error_info[0]})."
            pmac_data = [line.decode() for line in lines[0:-1]]
            if len(pmac_data) > 0:
                msg += f" Other data received before the error: {pmac_data}"
            log_error(self, msg)
            raise RuntimeError(msg)
        elif empty:
            pass
        else:
            raise RuntimeError("Received neither BELL nor ACK?")

        if len(pmac_data) == 1:
            return pmac_data[0]
        elif len(pmac_data) == 0:
            return None
        return pmac_data

    def _process_reply(self, reply_data, last_idx):
        print(reply_data)
        ack = reply_data.find(b"\x06", last_idx)
        if ack >= 0:
            # Expecting the <ACK> to be the last char
            # MAYBE?
            if len(reply_data) != ack + 1:
                msg = f"Received more data than expected: {reply_data}."
                log_error(self, msg)
                raise RuntimeError(msg)
            multi_line = reply_data[:-1].split(b"\r")
            multi_line = [line.decode() for line in multi_line if line]
            if len(multi_line) == 1:
                return multi_line[0]
            return multi_line

        bell = reply_data.find(b"\x07", last_idx)
        if bell >= 0:
            # received a BELL char -> ERROR
            multi_line = reply_data.split(b"\r")
            error = multi_line[-1]
            bell = multi_line[-2]
            if not error.startswith(b"ERR"):
                msg = (
                    f"Received a <BELL> char, but unexpected error msg format: {error}.\n"
                    f"(Full data: {reply_data})"
                )
                log_error(self, msg)
                raise RuntimeError(msg)
            msg = f"PMAC replied with an error code: {error}."
            if len(multi_line) > 3:
                msg += f"Data received before the error: {reply_data[:bell]}"
            log_error(self, msg)
            raise RuntimeError(msg)

        return False


# class PmacGetResponse(TurboPmacCommand):
#     request_type = PmacRequestType.VR_DOWNLOAD
#     request = PmacRequest.VR_PMAC_GETRESPONSE
#     reply_rx = "^(?P<response>.*)$"
#     eol = "\x06"


class _TurboPmacComm:
    """Communication class for the TurboPmac"""

    def __init__(self, config_dict):
        log_debug(self, "Initializing _TurboPmacComm.")
        self.pmac_comm = get_comm(config_dict, port=_PMAC_DEFAULT_PORT)

        global_map.register(self, children_list=[self.pmac_comm])

        # using a lock to make sure that command/requests
        # are "atomic"
        # because PMAC commands that expect a reply are actually
        #   decomposed into 2 sets of requests:
        # - send actual command, get ACK from PMAC
        # - to get the reply to the command, if any, send a GetBuffer
        #   request, then read the reply
        # Without the lock, bliss sends parallel requests to the PMAC
        #   and all replies get mixed up.
        self._lock = gevent.lock.BoundedSemaphore(value=1)
        log_debug(self, "Initialized _TurboPmacComm.")

    def host(self):
        "Returns a tuple (hostname, port)"
        return self.pmac_comm._host, self.pmac_comm._port

    def flush(self):
        with self._lock:
            self.pmac_comm.flush()
            command = PmacFlush(self.pmac_comm, value=_PMAC_FLUSH_TIMEOUT)
            return command.process()

    def _send_command(self, command_klass, *args, **kwargs):
        log_debug(self, "send_command %s (%s, %s)", command_klass, args, kwargs)
        command = command_klass(self.pmac_comm, *args, **kwargs)
        return command.process()

    def sendline(self, data):
        with self._lock:
            self._send_command(PmacSendline, data=data)

    def sendline_getbuffer(self, data=None, data_len=None, **kwargs):
        with self._lock:
            self._send_command(PmacSendline, data=data)
            data_len = data_len or _PMAC_MAX_BUFFER_SIZE
            return self._send_command(PmacGetBuffer, data_len=data_len)

    def pmac_version(self):
        return self.sendline_getbuffer(data="VERSION")

    def pmac_date(self):
        return self.sendline_getbuffer(data="DATE")

    def pmac_type(self):
        return self.sendline_getbuffer(data="TYPE")

    def read_i_register(self, i_reg, axis_address=None, cast_to=None):
        if axis_address is not None:
            axis_address = int(axis_address)
            command = f"I{axis_address}{i_reg}"
        else:
            command = f"I{i_reg}"
        log_debug(self, "Requesting register %s", command)
        reply = self.sendline_getbuffer(data=command)
        if cast_to:
            value = cast_to(reply)
        else:
            value = reply
        log_debug(self, "Received I register value %s=%s", command, value)
        return value

    def write_i_register(self, i_reg, value, axis_address=None):
        if axis_address is not None:
            axis_address = int(axis_address)
            command = f"I{axis_address}{i_reg:02d}={value}"
        else:
            command = f"I{i_reg}={value}"
        log_debug(self, "Writing to register %s", command)
        self.sendline(command)

    def read_jog_speed(self, address):
        """Reads an axis' jog speed

        Reads the Ixx22 register value.
        WARNING: units are in counts/msec.
        """
        velocity = self.read_i_register(22, axis_address=address, cast_to=float)
        return velocity

    def write_jog_speed(self, axis_address, jog_speed):
        """Sets an axis' jog speed

        Writes to the Ixx22 register value.
        WARNING: units are in counts/msec.
        """
        assert jog_speed >= 0
        self.write_i_register(22, jog_speed, axis_address=axis_address)

    def write_home_speed(self, axis_address, home_speed):
        """Sets an axis' home speed and direction.

        Writes to the Ixx23 register value.
        WARNING: units are in counts/msec.
        """
        self.write_i_register(23, home_speed, axis_address=axis_address)

    def read_home_speed(self, axis_address):
        """Reads an axis' home speed and direction

        Reads the Ixx23 register value.
        WARNING: units are in msec.
        """
        home_speed = self.read_i_register(23, axis_address=axis_address, cast_to=float)
        return home_speed

    def read_jog_accel_time(self, axis_address):
        """Reads an axis' acceleration time

        Reads the Ixx20 register value.
        WARNING: units are in msec.
        """
        accel_time = self.read_i_register(20, axis_address=axis_address, cast_to=float)
        return accel_time

    def write_jog_accel_time(self, axis_address, accel_time):
        """Sets an axis' acceleration time

        Writes to the Ixx20 register value.
        WARNING: units are in msec.
        """
        self.write_i_register(20, accel_time, axis_address=axis_address)

    def motor_status(self, axis_address):
        """Returns the state of the given axis.

        See Turbo SRM documentation on the ? command.
        """
        log_debug(self, "Requesting state for axis %s", axis_address)
        axis_address = int(axis_address)
        command = f"#{axis_address}?"
        status = self.sendline_getbuffer(data=command)
        log_debug(self, "Received state for axis %s : %s", command, status)
        return status

    def motor_position(self, axis_address):
        """PMAC's motor position"""
        log_debug(self, "Requesting position for axis %s", axis_address)
        axis_address = int(axis_address)
        m_pos = float(self.sendline_getbuffer(f"#{axis_address}P"))
        log_debug(self, "Received position for axis %s=%s", axis_address, m_pos)
        return m_pos

    def is_open_loop(self, axis_address):
        status = int(self.motor_status(axis_address), base=16)
        return (status & PMAC_MOTOR_STATUS.OPEN_LOOP.value[0]) != 0

    def open_loop(self, axis_address):
        self.sendline_getbuffer(f"#{axis_address}J:0")

    def close_loop(self, axis_address):
        self.sendline_getbuffer(f"#{axis_address}J/")

    def jog_stop(self, axis_address):
        """Sends a stop command."""
        log_debug(self, "Sending jog stop request for axis %s", axis_address)
        axis_address = int(axis_address)
        self.sendline(f"#{axis_address}J/")
        log_debug(self, "Jog stop request sent for motor %s", axis_address)

    def jog_to(self, axis_address, target):
        """
        Sends a "jog to position" command.
        """
        log_debug(
            self,
            "Sending a jog request for axis %s to position %s.",
            axis_address,
            target,
        )
        axis_address = int(axis_address)
        # this is just an extra check to make sure that the passed target
        # is a number
        target = float(target)
        self.sendline(f"#{axis_address}J={target}")
        log_debug(self, "Jog request sent for motor %s.", axis_address)

    def jog_relative(self, axis_address, delta):
        """
        Sends a "jog relative to current position" command.
        """
        log_debug(
            self,
            "Sending a jog request for axis %s, relative move with delta=%s.",
            axis_address,
            delta,
        )
        axis_address = int(axis_address)
        # this is just an extra check to make sure that the passed target
        # is a number.
        delta = float(delta)
        self.sendline(f"#{axis_address}J:{delta}")
        log_debug(self, "Relative jog request sent for motor %s.", axis_address)

    def jog(self, axis_address, direction):
        """
        Sends a "jog in the given direction" commnand.
        Direction: positive if direction > 0, negative otherwise.
        """
        log_debug(
            self,
            "Sending a jog request for axis %s (direction=%s).",
            axis_address,
            direction,
        )
        axis_address = int(axis_address)
        if direction > 0:
            op = "+"
        else:
            op = "-"
        self.sendline(f"#{axis_address}J{op}")
        log_debug(self, "jog%s request sent for motor %s.", op, axis_address)


class TurboPmac(Controller):
    """Bliss controller for DeltaTau's TurboPmac

    Methods to override:
     - TurboPmac._pmac_state
    """

    def __init__(self, *args, **kwargs):
        super(TurboPmac, self).__init__(*args, **kwargs)

        self._pmac_status = AxisState()
        self._pmac_status.create_state("HOMING", "Home search in progress")
        self._pmac_status.create_state("HOMED", "Home search complete")
        self._pmac_status.create_state("WARN", "Warning")
        # self.pmac_comm = None
        self.pmac_comm = _TurboPmacComm(self.config.config_dict)

        global_map.register(
            self, parents_list=["controllers"], children_list=[self.pmac_comm]
        )

        # self.axis_settings.config_setting["pmac_address"] = True

        # velocity and acceleration are not mandatory in config
        # self.axis_settings.config_setting["velocity"] = False
        # self.axis_settings.config_setting["acceleration"] = False

    def get_id(self, axis):
        return self.pmac_comm.pmac_version()

    def initialize(self):
        super(TurboPmac, self).initialize()
        log_debug(self, "Initializing.")
        self.axis_settings.config_setting["velocity"] = False
        self.axis_settings.config_setting["acceleration"] = False
        self.axis_settings.config_setting["steps_per_unit"] = False
        log_debug(self, "Initialized.")

    def initialize_hardware(self):
        super(TurboPmac, self).initialize_hardware()

        # I3=2
        # IO handshake control, turbo srm page 85
        # or PMAC user manual p383
        # command is ack by PMAC with an <ACK>  (ascii 0x06)
        # invalid command is ack with a <BELL>  (ascii 0x07)
        # messages are sent as DATA <CR> [ DATA <CR> ... ] <ACK>
        # <CR> = 0x0D or \r
        # <LF> = 0x0A or \n
        self.pmac_comm.write_i_register(3, 2)

        # I4=0
        # communication integrity mode
        # 0 = checksum disabled
        self.pmac_comm.write_i_register(4, 0)

        # I100=0
        # deactivate motor X (here X = 1)

        host, port = self.pmac_comm.host()
        pmac_version = self.pmac_comm.pmac_version()
        pmac_type = self.pmac_comm.pmac_type()
        pmac_date = self.pmac_comm.pmac_date()
        info = (
            f"PMAC:\n"
            f"   - host : {host}:{port},\n"
            f"   - version : {pmac_version},\n"
            f"   - type : {pmac_type},\n"
            f"   - date : {pmac_date}."
        )
        log_info(self, f"Connected to {info}.")

    def raw_write_read(self, com):
        if len(com) > _PMAC_MAX_BUFFER_SIZE:
            raise NotImplementedError(
                f"Data of more than {_PMAC_MAX_BUFFER_SIZE} chars not supported yet."
            )
        reply = self.pmac_comm.sendline_getbuffer(com)
        if reply is None:
            return ""
        if isinstance(reply, (list,)):
            reply = "\r".join(reply)
        return reply

    def raw_write(self, com):
        self._write_multiline(com)
        # if len(com) > _PMAC_MAX_BUFFER_SIZE:
        #     log_debug(self, "Message length > max buffer size, sending as multiline.")
        #     self._write_multiline(com)
        # else:
        #     self.pmac_comm.sendline(com)

    def initialize_axis(self, axis):
        address = self._pmac_address(axis)
        read_only = axis.config.config_dict.get("read_only", False)

        # used to get the axis status:
        # if commutation: not moving if status bit
        #   PMAC_MOTOR_STATUS.PHASED_MOT is not set
        # if not commutation: PHASED_MOT is not used
        # commutation = int(self.pmac_comm.sendline_getbuffer(f"I{address}01"))
        # setattr(axis, "pmac_commutation", commutation)
        # print("COMMU", axis)

        # if not read only, we should have the default mandatory
        # settings in the yaml
        # TODO : this needs more thought...
        if not read_only:
            try:
                axis.config.get("velocity")
                axis.config.get("steps_per_unit")
                axis.config.get("acceleration")
            except Exception as ex:
                if not ex.args:
                    ex.args = (f"PMAC axis {axis.name}:",)
                else:
                    ex.args = (f"PMAC axis {axis.name} : " + ex.args[0],) + ex.args[1:]
                raise

        name = axis.name
        log_debug(self, "Initializing axis %s, with address %s.", name, address)
        # axis.pmac_address = address
        axis.pmac_read_only = read_only

        log_debug(self, "Initialized axis %s", name)

    def initialize_hardware_axis(self, axis):
        super(TurboPmac, self).initialize_hardware_axis(axis)
        # setting the motor maximum jog/home acceleration, forced to 1000
        # TODO : this value is set to 1000 to reproduce the behaviour of
        #   another famous program...
        pmac_address = self._pmac_address(axis)
        self.pmac_comm.write_i_register(19, 1000.0, axis_address=pmac_address)
        # setting the motor maximum jog/home s-curve time to 0
        #  so that only Ixx20 is used
        self.pmac_comm.write_i_register(21, 0, axis_address=pmac_address)

        addr = pmac_address * 8
        # M161->D:$000088
        # M register $000088 now points to the motor #1 commanded position
        self.pmac_comm.sendline(f"M{pmac_address}61->D:${addr:0>5x}8")
        # M162->D:$00008B
        # M register $00008B now points to the motor #1 actual position
        self.pmac_comm.sendline(f"M{pmac_address}62->D:${addr:0>5x}B")

    def pmac_motor_status_to_str(self, axis, print_stdout=True):
        pmac_address = self._pmac_address(axis)
        status = self.pmac_comm.motor_status(pmac_address)
        status = int(status, base=16)
        status_str = pmac_motor_status_to_str(status)
        if print_stdout:
            print(status_str)
        else:
            return status_str

    def _update_deceleration_rate(self, axis):
        """
        Updating the Ixx15 variable (motor abort/limit deceleration rate)

        Warning: should not be set to 0.
        """
        # TODO : should read register instead of axis.acceleration
        # because not sure that it is set at this time
        pmac_address = self._pmac_address(axis)
        # accel in steps/s-2
        acceleration = axis.acceleration * abs(axis.steps_per_unit)
        # decel in steps/ms-2
        # TODO : the factor is there to reproduce the behaviour of
        #   another famous program... why the factor?
        decel = 2 * acceleration / 1000**2.0
        log_debug(
            self, "Setting motor #%s deceleration rate to %s.", pmac_address, decel
        )
        if decel == 0:
            raise ValueError(f"Deceleration rate should not be {decel}.")
        # TODO
        # self.pmac_comm.write_i_register(15, decel, axis_address=pmac_address)

    def set_velocity(self, axis, new_velocity):
        """
        Sets velocity in steps/s-1
        """
        if self._pmac_axis_read_only(axis):
            log_debug(self, "Cannot set velocity, axis %s is read_only.", axis.name)
            return
        pmac_address = self._pmac_address(axis)
        log_debug(
            self,
            "TurboPmac: setting axis %s velocity to %s",
            pmac_address,
            new_velocity,
        )
        # pmac is in cts/ms, new_velocity is in cts/s, hence the 10**-3 factor
        pmac_velocity = new_velocity / 1000.0
        self.pmac_comm.write_jog_speed(pmac_address, pmac_velocity)

    def read_velocity(self, axis):
        """
        Returns velocity in steps/s-1
        """
        pmac_address = self._pmac_address(axis)
        velocity = self.pmac_comm.read_jog_speed(pmac_address)
        # pmac is in cts/ms, bliss expects units/s, hence the 10**3
        velocity = velocity * 1000
        log_debug(self, "TurboPmac: axis %s velocity is %s", pmac_address, velocity)
        return velocity

    def set_acceleration(self, axis, new_acceleration):
        if self._pmac_axis_read_only(axis):
            log_debug(
                self, "set_acceleration: axis %s defined as read only.", axis.name
            )
            return
        pmac_address = self._pmac_address(axis)
        # pmac doesnt have acceleration, but has acceleration time
        # PMAC's acceleration time is in msec
        # new_acceleration is in steps.s-2
        # dt = 1000(ms) * dv / a
        steps_velocity = axis.velocity * abs(axis.steps_per_unit)
        accel_time = int(0.5 + 1000.0 * steps_velocity / new_acceleration)
        log_debug(self, "1000*accel_time=%s/%s", steps_velocity, new_acceleration)
        log_debug(
            self,
            "Setting axis %s acceleration "
            "to %s "
            "(i.e: %sms of acceleration time).",
            pmac_address,
            new_acceleration,
            accel_time,
        )
        if accel_time == 0:
            i21 = self.pmac_comm.read_i_register(21, axis_address=pmac_address)
            if i21 == 0:
                raise ValueError("Acceleration time should not be 0.")
        self._update_deceleration_rate(axis)
        self.pmac_comm.write_jog_accel_time(pmac_address, accel_time)

    def read_acceleration(self, axis):
        pmac_address = self._pmac_address(axis)
        # pmac doesnt have acceleration, but has acceleration time
        # PMAC's acceleration time is in msec
        # BLISS's acceleration is in unit.s-2
        # a = 1000(ms) * dv / dt
        accel_time = self.pmac_comm.read_jog_accel_time(pmac_address)
        steps_velocity = axis.velocity * abs(axis.steps_per_unit)
        accel = 1000.0 * steps_velocity / accel_time
        log_debug(
            self,
            "TurboPmac: axis %s acceleration is %s steps/s^2.",
            pmac_address,
            accel,
        )
        return accel

    def pmac_is_open_loop(self, axis):
        return self.pmac_comm.is_open_loop(self._pmac_address(axis))

    def pmac_open_loop(self, axis):
        if not self.pmac_is_open_loop(axis):
            self.pmac_comm.open_loop(self._pmac_address(axis))
        ex = RuntimeError(f"{self.name}, axis {axis.name}: " "Failed to open the loop.")
        with gevent.Timeout(5.0, ex):
            while not self.pmac_is_open_loop(axis):
                gevent.sleep(0.2)

    def pmac_close_loop(self, axis):
        if self.pmac_is_open_loop(axis):
            self.pmac_comm.close_loop(self._pmac_address(axis))
        ex = RuntimeError(f"{self.name}, axis {axis.name}: " "Failed to open the loop.")
        with gevent.Timeout(5.0, ex):
            while self.pmac_is_open_loop(axis):
                gevent.sleep(0.2)

    def state(self, axis):
        # pmac motor status is a 12 characters hexadecimal word (2x24 bits)
        # see TURBO SRM documentation about the "?" (report moto status)
        # command.
        pmac_address = self._pmac_address(axis)
        pmac_status = self.pmac_comm.motor_status(pmac_address)
        pmac_status = int(pmac_status, base=16)
        return self._pmac_status_to_bliss_state(axis, pmac_status)

    def _pmac_status_to_bliss_state(self, axis, pmac_status):
        state = self._pmac_status.new()
        state_names = self.pmac_status_to_bliss_state_names(axis, pmac_status)
        user_state_names = self._pmac_state(axis, tuple(state_names), pmac_status)
        for state_name in user_state_names:
            state.set(state_name)
        return state

    def pmac_status_to_bliss_state_names(self, axis, pmac_status):
        # to be confirmed:
        # READY (or):
        #  - move timer off
        #  - open loop  (?)
        #  - in-position
        #  - desired velocity != 0 (?)
        # MOVING (or):
        #  - move timer on
        #  - homing
        #  - desired velocity != 0
        state_names = set()

        fault = (
            PMAC_MOTOR_STATUS.FATAL_FOL_ERR.value[0]
            | PMAC_MOTOR_STATUS.INTEG_FATAL_FOL_ERR.value[0]
            | PMAC_MOTOR_STATUS.PHAS_REF_ERR.value[0]
            | PMAC_MOTOR_STATUS.I2T_AMPL_FAULT_ERR.value[0]
            | PMAC_MOTOR_STATUS.AMPLI_FAULT.value[0]
        )

        if pmac_status & PMAC_MOTOR_STATUS.HOMED.value[0]:
            state_names |= {"HOMED"}

        if pmac_status & fault:
            state_names |= {"FAULT"}
        else:
            if pmac_status & PMAC_MOTOR_STATUS.LIMNEG.value[0]:
                state_names |= {"LIMNEG"}
            if pmac_status & PMAC_MOTOR_STATUS.LIMPOS.value[0]:
                state_names |= {"LIMPOS"}
            if pmac_status & PMAC_MOTOR_STATUS.MOVE_TIMER.value[0]:
                state_names |= {"MOVING"}
            elif pmac_status & PMAC_MOTOR_STATUS.IN_POSITION.value[0]:
                #     # state_names -= "MOVING"
                state_names |= {"READY"}
            else:
                if not pmac_status & PMAC_MOTOR_STATUS.MOT_ACTIVATED.value[0]:
                    state_names |= {"OFF"}
                elif (
                    int(self.pmac_comm.sendline_getbuffer(f"I{axis.pmac_address}01"))
                    not in (0, 2)
                    and pmac_status & PMAC_MOTOR_STATUS.PHASED_MOT.value[0]
                ):
                    raise NotImplementedError(
                        f"Case not tested (axis={axis.name}): "
                        f"I{axis.address}01={axis.pmac_commutation} "
                        f'and status bit "Phased Motor" set.'
                    )
                    # state_names |= {"READY"}
                elif pmac_status & PMAC_MOTOR_STATUS.OPEN_LOOP.value[0]:
                    state_names |= {"READY"}
                else:
                    state_names |= {"MOVING"}
        return state_names

    def _pmac_state(self, axis, state_names, pmac_status):
        """
        Override this method to customize the axis state.

        Args:
            axis: the axis
            state_names: default bliss state named corresponding to the status
            pmac_status (int): the 24 bits returned by the '#x?' command, where
                x is the axis address.
        """
        return state_names

    def _pmac_axis_read_only(self, axis):
        return getattr(axis, "pmac_read_only", False)

    def _pmac_address(self, axis):
        try:
            pmac_address = axis.pmac_address
        except AttributeError:
            pmac_address = axis.config.get("pmac_address")
            setattr(axis, "pmac_address", pmac_address)
        if pmac_address is None or pmac_address <= 0:
            raise InvalidAddressException(f"Invalid address for axis {axis.name}.")
        return pmac_address

    def read_position(self, axis):
        axis_address = self._pmac_address(axis)
        reply = self.pmac_comm.sendline_getbuffer(
            f"#{axis_address}?I{axis_address}08M{axis_address}61M{axis_address}62"
        )
        pmac_status = int(reply[0], base=16)
        ix08, mxx61, mxx62 = np.float64(reply[1:])
        bliss_status = self._pmac_status_to_bliss_state(axis, pmac_status)

        ready = "READY" in bliss_status
        if ready:
            m_pos = mxx61 / (32 * ix08)
        else:
            m_pos = mxx62 / (32 * ix08)
        return m_pos

    def set_position(self, axis, new_position):
        # we set commanded position (Mxx61)
        axis_address = self._pmac_address(axis)
        reply = self.pmac_comm.sendline_getbuffer(f"I{axis_address}08")
        ix08 = int(reply)
        m_pos = new_position * 32 * ix08
        self.pmac_comm.sendline_getbuffer(f"M{axis_address}62={m_pos}")
        return self.read_position(axis)

    def stop(self, axis):
        self.pmac_comm.jog_stop(self._pmac_address(axis))

    def pmac_stop_all(self, init_only=False):
        if init_only:
            axes = self._axes
        else:
            axes = [
                self.get_axis(axis["name"])
                for axis in self.config.config_dict.get("axes")
            ]
        for axis in axes:
            log_info(self, f"Stopping {axis.name}.")
            self.stop(axis)

    def prepare_move(self, motion):
        if self._pmac_axis_read_only(motion.axis):
            raise RuntimeError(f"Axis {motion.axis.name}" f" defined as read only.")

    def start_one(self, motion):
        if self._pmac_axis_read_only(motion.axis):
            raise RuntimeError(f"Axis {motion.axis.name} defined as read only.")
        axis_address = self._pmac_address(motion.axis)
        log_debug(self, "start_one on axis %s.", axis_address)
        if not motion.type == "move":
            raise ValueError(f"Unsupported motion type: {motion.type}.")

        if motion.target_pos is not None:
            self.pmac_comm.jog_to(axis_address, motion.target_pos)
        elif motion.delta is not None:
            self.pmac_comm.jog_relative(axis_address, motion.delta)
        else:
            raise ValueError("target_pos and delta are both None.")

    def start_jog(self, axis, velocity, direction):
        """
        Starts a jog in the given direction.
        """
        if self._pmac_axis_read_only(axis):
            raise RuntimeError(f"Axis {axis.name} defined as read only.")
        axis_address = self._pmac_address(axis)
        log_debug(
            self,
            "start_jog on axis %s, direction=%s, velocity=%s.",
            axis_address,
            direction,
            velocity,
        )
        self.set_velocity(axis, velocity)
        self.pmac_comm.jog(axis_address, direction)

    def set_on(self, axis):
        """
        Writes 1 to register Ixx00
        """
        address = self._pmac_address(axis)
        log_debug(self, "Enabling motor %s.", address)
        self.pmac_comm.write_i_register(0, 1, axis_address=address)

    def set_off(self, axis):
        """
        Writes 0 to register Ixx00
        """
        address = self._pmac_address(axis)
        log_debug(self, "Disabling motor %s.", address)
        self.pmac_comm.write_i_register(0, 0, axis_address=address)

    def limit_search(self, axis, limit):
        """
        Starts a limit search (sends a JOG in the given direction).
        """
        if self._pmac_axis_read_only(axis):
            raise RuntimeError(f"Axis {axis.name} defined as read only.")
        direction = 1 if limit > 0 else -1
        self.pmac_comm.jog(self._pmac_address(axis), direction)

    def home_search(self, axis, direction):
        if self._pmac_axis_read_only(axis):
            raise RuntimeError(f"Axis {axis.name} defined as read only.")
        address = self._pmac_address(axis)
        log_debug(
            self, "Starting a home search for axis %s (dir=%s).", axis.name, direction
        )
        direction = 1 if direction > 0 else -1
        # home speed in counts/ms !!
        # TODO: read the Ixx22 register instead of recalculating the speed
        home_speed = direction * abs(axis.velocity * axis.steps_per_unit) / 1000.0
        self.pmac_comm.write_home_speed(address, home_speed)
        self.pmac_comm.sendline(f"#{address}HOME")

    def home_state(self, axis):
        axis_state = axis.hw_state

        if "HOMING" in axis_state:
            axis_state.set("MOVING")
            try:
                axis_state.unset("READY")
            except ValueError:
                pass
        return axis_state

    def pmac_home_states(self, *axis):
        """
        Returns an OrderedDict containing True or False for each given axis,
        depending on the "homed" state.
        Keys: axis name
        Value: True if the axis' state contains HOMED, False otherwise
        """
        states = OrderedDict()
        for ax in axis:
            if not isinstance(ax, (Axis,)):
                ax = self.get_axis(ax)
            axis_name = ax.name
            state = ax.hw_state
            homed = "HOMED" in state
            states[axis_name] = homed
        return states

    def _pmac_reboot(self, **kwargs):
        """
        Reboots the PMAC.

        Mandatory keyword: confirm=True
        """
        if kwargs.get("confirm") is True:
            delay = 5
            try:
                for i in range(delay):
                    log_info(self, f"Rebooting the PMAC {self.name} in {delay-i}s.")
                    gevent.sleep(1)
                self.pmac_comm.sendline("$$$")
            except Exception as ex:
                log_info(self, f"PMAC {self.name}: reboot cancelled. (reason:{ex})")
        else:
            raise RuntimeError(
                f'ERROR: reboot: Pmac {self.name}, missing keyword "confirm".'
            )

    def _write_multiline(self, data):
        print("============== MUL")
        multilines = []
        for line in re.split("\r|\n", data):
            match = re.match("^([^;]+)", line.lstrip())
            if match:
                multilines += [match.groups()[0].rstrip()]
        # if len(multilines) > _PMAC_MAX_BUFFER_SIZE:
        #     raise NotImplementedError(
        #         f"Data of more than {_PMAC_MAX_BUFFER_SIZE} chars not supported yet."
        #     )
        # TODO: implement VR_PMAC_WRITEBUFFER
        for line in multilines:
            print(line)
            self.pmac_comm.sendline_getbuffer(line)
