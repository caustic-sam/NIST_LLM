# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

""" Meerstetter communication protocol related

    The code for classes MeComProtocol and TECFamilyProtocol
    is based on the code found in the file
    ting:~blissadm/server/src/Temperature/LTR1200.py,
    which was created to be used by LTR1200TemperatureDS
    Tango DS.
    In the class TECFamilyProtocol only "base" functions were
    retained and the functions for setting/getting different
    features are rather implemented in the "low-level" class
    tec1089 in the file tec1089.py.

    Actually, the names and the class famylies are changed because
    ltr1200 is only the case while the communication protocol concerns
    the TEC-Family: TEC-1089, TEC-1090, TEC-1091, TEC-1122, TEC-1123.
    ID06 uses the TEC-1089.

"""

import struct

######################################################################
###########################                ###########################
########################### MeCOM PROTOCOL ###########################
###########################                ###########################
######################################################################
#
# Frame Fields:
# ------------
#     8 bits: control source field ("!" for input, "#" for output)
#    16 bits: device address
#    32 bits: random sequence identifier
# N * 8 bits: client command (so called payload)
#    32 bits: frane CRC checksum
#     8 bits: frame terminator \r (eof = end of frame)
#
######################################################################


class MeComProtocol:
    def __init__(self, sock_comm, dev_addr):
        self.sequence = 0
        self._sock = sock_comm
        self.dev_addr = dev_addr

        # Seb suggested to use connect to regsiter a louie callback to parse and skip
        # the connection "greetings" from the controller but this does not work
        # def drop_greetings(connected: bool):
        #    if connected:
        #        self._sock.readline(eol=b"\r\n\r\n")

        # connect(self._sock, "connect", drop_greetings)

    def putget(self, cmd, anslen, eof):
        frame = self.build_frame(cmd, eof)
        return self._putget(frame, cmd, anslen, eof)

    def _putget(self, frame, cmd, anslen, eof):
        with self._sock._lock:
            if not self._sock._connected:
                self._sock.connect()
                # Drop greetings
                self._sock.readline(eol=b"\r\n\r\n")

            answer = self._sock.write_readline(frame.encode(), eol=eof.encode())

        if answer == "":
            raise RuntimeError("MeComProtocol::_putget: Socket connection broken")

        resp = (frame[:7].replace("#", "!")).encode()
        if answer.startswith(resp):
            if answer[7] == "+":
                _ = answer[8:10]
                # Errors:
                #    "Unknown error"
                #    "Command not available"
                #    "Device is busy"
                #    "General communication error"
                #    "Format error"
                #    "Parameter is not available"
                #    "Parameter is read only"
                #    "Value out of range"
                #    "Channel not available"
            else:
                if cmd[0] == "?":  # query commands
                    assert len(answer) == (11 + anslen), "answer length not expected."
                    answ = answer[7 : anslen + 7]
                    # blacrc = self._crc16_algorithm(resp + answ)
                    return answ
                else:  # set commands
                    return "ACK"

    def build_frame(self, payload, eof):
        frame = []

        frame.extend("%02x" % (self.dev_addr))
        frame.extend("%04x" % (self._assign_sequence_number()))
        frame.extend(payload)
        frame.insert(0, "#")

        frame = "".join(frame)
        self.CRC = self._crc16_algorithm(frame.encode())

        if self.CRC > 0xFFFF:
            raise RuntimeError("too large numeric CRC: %x." % (self.CRC))

        frame = frame + ("%04x%s" % (self.CRC, eof))

        return "".join(frame).upper()

    def _crc16_algorithm(self, frame):
        frame = frame.upper()
        crc = 0
        genpoly = 0x1021

        for c in frame:
            c2 = (c & 0x00FF) << 8
            crc = crc ^ c2
            for i in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ genpoly
                else:
                    crc = crc << 1
            crc &= 0xFFFF

        return crc

    def _assign_sequence_number(self):

        if self.sequence < 65534:
            self.sequence += 1
        else:
            self.sequence = 0

        return self.sequence


######################################################################
############################              ############################
############################  TEC Family  ############################
############################              ############################
######################################################################


class TECFamilyProtocol:
    def __init__(self, sock_comm, dev_addr):
        self.mecom = MeComProtocol(sock_comm, dev_addr)

    def putget(self, command, anslen=0, EOF="\r"):
        ret = self.mecom.putget(command, anslen, EOF)
        return ret

    def get_model(self):
        """get firmware identification string"""
        self.model = self.putget("?IF", 20)
        return self.model

    def _get_parameter(self, id, anslen, format, channel):
        if id > 0xFFFF:
            raise RuntimeError("wrong parameter id: %x." % (id))

        if channel > 0xFF:
            raise RuntimeError("wrong parameter channel: %x." % (channel))

        # ? Value Read
        payload = ["?", "V", "R"]
        payload.extend("%04x" % (id))
        payload.extend("%02x" % (channel))

        answer = self.putget("".join(payload), anslen)

        if answer is not None:
            if format == "int":
                answer = int(answer, 16)
            elif format == "float":
                answer = answer.decode()
                answer = struct.unpack(">f", bytes.fromhex(answer))[0]
            else:
                raise RuntimeError("wrong parameter format: %s." % (format))

        return answer

    def _set_parameter(self, id, parameter, format, channel):
        if id > 0xFFFF:
            raise RuntimeError("wrong parameter id: %x." % (id))

        if channel > 0xFF:
            raise RuntimeError("wrong parameter channel: %x." % (channel))

        # Value Set
        payload = ["V", "S"]
        payload.extend("%04x" % (id))
        payload.extend("%02x" % (channel))

        if format == "int":
            parameter = struct.pack(">i", parameter).hex()
        elif format == "float":
            parameter = struct.pack(">f", parameter).hex()

        payload.extend("%s" % (parameter))
        answer = self.putget(payload)

        return answer
