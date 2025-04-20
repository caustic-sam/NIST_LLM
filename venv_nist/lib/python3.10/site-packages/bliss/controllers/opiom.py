# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import os
import struct
import time

from bliss import global_map
from bliss.comm.util import get_comm, SERIAL
from bliss.comm import serial
from bliss.common.greenlet_utils import KillMask, protect_from_kill
from bliss.common.switch import Switch as BaseSwitch
from bliss.common.user_status_info import status_message
from bliss.config.conductor.client import remote_open

from bliss.common.logtools import log_debug

OPIOM_PRG_ROOT = "/users/blissadm/local/isg/opiom"


class Opiom:
    FSIZE = 256

    def __init__(self, name, config):
        self.name = name

        self._cnx = get_comm(config, ctype=SERIAL, timeout=1)

        global_map.register(self, children_list=[self._cnx], tag=f"opiom:{name}")

        self.__program = config.get("program", "default")
        default_prg_root = config.get("opiom-prg-root", OPIOM_PRG_ROOT)
        self.__base_path = config.get("opiom_prg_root", default_prg_root)

        program_path = config.get("program-path")
        if program_path is not None:
            pg_path = os.path.splitext(program_path)[0]
            self.__base_path, self.__program = os.path.split(pg_path)

        # Sometimes, have to talk twice to the OPIOM in order to get the first proper answer.
        for timeout in [0.05, 1.0]:
            try:
                msg = self.comm("?VER", timeout=timeout)
            except serial.SerialTimeout:
                msg = ""

            if msg.startswith("OPIOM"):
                break
        else:
            _err_msg = f"No opiom connected at {self._cnx}"
            raise IOError(_err_msg)

        self.comm("MODE normal")

    def __close__(self):
        self._cnx.close()

    def __info__(self):
        info_str = "OPIOM\n"
        info_str += self._cnx.__info__()
        info_str += "---------------------------------\n"
        info_str += self.info()
        return info_str

    def info(self):
        return self.comm("?INFO")

    def source(self):
        return self.comm("?SRC", timeout=30.0)

    def prog(self):
        info = self.info()
        for line in info.split("\n"):
            if line.startswith("PLD prog:"):
                return line.split(":")[1].strip("\n\t ")

    def error(self):
        return self.comm("?ERR")

    def registers(self):
        return {
            "IM": int(self.comm("?IM"), base=16),
            "IMA": int(self.comm("?IMA"), base=16),
        }

    def inputs_stat(self):
        input_front = int(self.comm("?I"), base=16)
        input_back = int(self.comm("?IB"), base=16)

        self._display_bits("I", input_front)
        self._display_bits("IB", input_back)

    def outputs_stat(self):
        output_front = int(self.comm("?O"), base=16)
        output_back = int(self.comm("?OB"), base=16)

        self._display_bits("O", output_front)
        self._display_bits("OB", output_back)

    def raw_write(self, msg):
        self._cnx.write(msg)

    def raw_bin_write(self, binmsg):
        nb_block = len(binmsg) // self.FSIZE
        nb_bytes = len(binmsg) % self.FSIZE
        lrc = (nb_bytes + nb_block + sum([x for x in binmsg])) & 0xFF
        rawMsg = struct.pack(
            "BBB%dsBB" % len(binmsg), 0xFF, nb_block, nb_bytes, binmsg, lrc, 13
        )
        self._cnx.write(rawMsg)

    def comm_ack(self, msg):
        return self.comm("#" + msg)

    @protect_from_kill
    def comm(self, msg, timeout=None, text=True):
        self._cnx.open()
        with self._cnx._lock:
            self._cnx._write((msg + "\r\n").encode())
            if msg.startswith("?") or msg.startswith("#"):
                msg = self._cnx._readline(timeout=timeout)
                if msg.startswith("$".encode()):
                    msg = self._cnx._readline("$\r\n".encode(), timeout=timeout)
                log_debug(self, "Read %s" % msg.strip("\n\r".encode()))
                if text:
                    return (msg.strip("\r\n".encode())).decode()
                else:
                    return msg.strip("\r\n".encode())

    def load_program(self, prog_name=None):
        log_debug(self, "check PLID")
        pldid = self.comm("?PLDID")
        if prog_name is None:
            prog_name = self.__program
        if prog_name == "default":
            if pldid == "255":
                # already default
                log_debug(self, "prog is already the default one")
                return
            else:
                print("Uploading default program")
        else:
            try:
                file_pldid, file_project = self._getFilePLDIDandPROJECT(prog_name)
            except ValueError:
                # invalid unpacking
                raise IOError(
                    "opiom %s: cannot find program %s" % (str(self), prog_name)
                )

            s_pldid = str(pldid).encode()
            if file_pldid and file_pldid != s_pldid:
                print("Uploading opiom program, please wait")
                srcsz = int(self.comm("?SRCSZ").split()[0])
                offsets, opmfile = self._getoffset(prog_name)
                if (offsets["src_c"] - offsets["src_cc"]) < srcsz:
                    SRCST = offsets["src_cc"]
                    srcsz = offsets["src_c"] - offsets["src_cc"]
                else:
                    SRCST = offsets["src_c"]
                    srcsz = offsets["jed"] - offsets["src_c"]
                binsz = offsets["size"] - offsets["jed"]

                sendarray = opmfile[SRCST : SRCST + srcsz]
                sendarray += opmfile[offsets["jed"] :]
            else:
                # program already loaded
                log_debug(
                    self,
                    "No need to reload opiom program: PLDID did not change %s" % pldid,
                )
                return

        log_debug(self, "switch to MODE program")
        if self.comm_ack("MODE program") != "OK":
            raise IOError("Can't program opiom %s" % str(self))

        if prog_name == "default":
            log_debug(self, "require to load internal default program")
            ans = self.comm_ack("PROG DEFAULT")
            sendarray = []
        else:
            _cmd = 'PROG %d %d %d %d "%s"' % (
                binsz,
                srcsz,
                self.FSIZE,
                int(file_pldid),
                file_project,
            )
            log_debug(self, f"Send prog command: \n{_cmd}")
            ans = self.comm_ack(_cmd)

        if ans != "OK":
            self.comm("MODE normal")
            raise IOError("Can't start programming opiom %s" % str(self))
        log_debug(self, "Program loading started OK")

        with status_message() as update:
            for frame_n, index in enumerate(range(0, len(sendarray), self.FSIZE)):
                with KillMask():
                    cmd = "#*FRM %d\r" % frame_n
                    self.raw_write(cmd.encode())
                    update("")
                    update(f"FRAME {frame_n}")
                    self.raw_bin_write(sendarray[index : index + self.FSIZE])
                    answer = self._cnx.readline("\r\n".encode())
                    if answer[-2:] == b"OK":
                        continue
                    raise RuntimeError(
                        "Load program: [%s] returned [%s]" % (cmd.strip(), answer)
                    )

            log_debug(self, "waiting end programming")
            while True:
                time.sleep(0.1)  # Relax a bit the pressure on OPIOM to avoid problems.
                stat_num = self.comm("?PSTAT")
                update("")
                update(f"{stat_num}")
                log_debug(self, "Load %s" % stat_num)
                try:
                    stat, percent = stat_num.split()
                except ValueError:
                    stat = stat_num
                    break
        return stat == "DONE"

    def _display_bits(self, prefix, bits):
        for i in range(1, 9):
            print("%s%d\t" % (prefix, i), end=" ")
        print()
        for i in range(8):
            if (bits >> i) & 0x1:
                print("1\t", end=" ")
            else:
                print("0\t", end=" ")

        print()

    def _getoffset(self, prog_name):
        with remote_open(os.path.join(self.__base_path, prog_name + ".opm")) as f:
            line = f.read(14)
            f.seek(0)
            opmfile = f.read()
            size = f.tell()
        header, src, src_cc, src_c, jed = struct.unpack("<5H", line[3:13])
        return (
            {
                "header": header,
                "src": src,
                "src_cc": src_cc,
                "src_c": src_c,
                "jed": jed,
                "size": size,
            },
            opmfile,
        )

    def _getFilePLDIDandPROJECT(self, prog_name):
        TOKEN = b"#pldid#"
        PROJECT_TOKEN = b"#project#"
        with remote_open(os.path.join(self.__base_path, prog_name + ".opm")) as f:
            begin = -1
            for line in f:
                begin = line.find(TOKEN)
                if begin > -1:
                    break
            if begin > -1:
                subline = line[begin + len(TOKEN) :]
                end = subline.find(TOKEN)
                pldid = subline[:end]

                begin = line.find(PROJECT_TOKEN)
                subline = line[begin + len(PROJECT_TOKEN) :]
                project = subline[: subline.find(PROJECT_TOKEN)]
                return pldid, project

    def test(self):
        """
        Perform a set of OPIOM commands
        """
        commands_list = [
            "?I",
            "?IB",
            "?O",
            "?OB",
            "?IM",
            "?SP",
            "?DEFIM",
            "?OM",
            "?CNT 1",
            "?CNT 2",
            "?CNT 3",
            "?CNT 4",
            "?CNT 5",
            "?SCNT 1",
            "?SCNT 2",
            "?SCNT 3",
            "?SCNT 4",
            "?SCNT 5",
            "?VCNT 1",
            "?VCNT 2",
            "?VCNT 3",
            "?VCNT 4",
            "?VCNT 5",
            "?FORMAT",
            "?CHAIN",
            "?VER",
            "?PLDID",
            "?NAME",
        ]
        for cmd in commands_list:
            print(f"{cmd}  ->  {self.comm(cmd)}")

    def commands_list(self):
        """
        Display list of available commands.
        """
        print(self.comm("?HELP"))


class Switch(BaseSwitch):
    """
    This class wraps opiom commands to emulate a switch.

    This class is well-suited to be used as external_control for an icepap
    shutter.

    The configuration may look like this:

    .. code-block::

        opiom: $opiom_name
        register: IMA
        mask: 0x3
        shift: 1
        states:
           - label: OPEN
             value: 1
           - label: CLOSED
             value: 0
           - label: MUSST
             value: 2
           - label: COUNTER_CARD
             value: 3
    """

    def __init__(self, name, config):
        BaseSwitch.__init__(self, name, config)
        self.__opiom = None
        self.__register = None
        self.__mask = None
        self.__shift = None
        self.__states = dict()

    def _init(self):
        config = self.config
        self.__opiom = config["opiom"]
        self.__register = config["register"]
        self.__mask = config["mask"]
        self.__shift = config["shift"]
        for state in config["states"]:
            label = state["label"]
            value = state["value"]
            self.__states[label] = value

    def _set(self, state):
        value = self.__states.get(state)
        if value is None:
            raise RuntimeError("State %s don't exist" % state)
        mask = self.__mask << self.__shift
        value <<= self.__shift
        cmd = "%s 0x%x 0x%x" % (self.__register, value, mask)
        self.__opiom.comm_ack(cmd)

    def _get(self):
        cmd = "?%s" % self.__register
        value = int(self.__opiom.comm_ack(cmd), base=16)
        value >>= self.__shift
        value &= self.__mask
        for label, state_value in self.__states.items():
            if state_value == value:
                return label
        return "UNKNOWN"

    def _states_list(self):
        return list(self.__states.keys())
