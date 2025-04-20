# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import enum
from functools import wraps
from bliss.comm.util import get_comm, get_comm_type, TCP, SERIAL
from bliss.controllers.motor import Controller
from bliss.common.axis.state import AxisState
from bliss.common.utils import object_method
from bliss.comm.exceptions import CommunicationTimeout
from bliss import global_map
from .pi_gcs import get_error_str

"""
Bliss controller for controlling the Physik Instrumente hexapod
controllers 850 and 887.

The Physik Instrument Hexapod M850 is a hexapod controller with
a serial line interface.
The Physik Instrument Hexapod C887 is a hexapod controller with
a serial line and socket interfaces. Both of them can be used.

config example:
- class PI_HEXA
  model: 850 # 850 or 887 (optional)
  serial:
    url: ser2net://lid133:28000/dev/ttyR37
  axes:
    - name: hexa_x
      channel: X
    - name: hexa_y
      channel: Y
    - name: hexa_z
      channel: Z
    - name: hexa_u
      channel: U
    - name: hexa_v
      channel: V
    - name: hexa_w
      channel: W
"""


def _atomic_communication(fn):
    @wraps(fn)
    def f(self, *args, **kwargs):
        with self._cnx.lock:
            return fn(self, *args, **kwargs)

    return f


class PI_HEXA(Controller):
    COMMAND = enum.Enum(
        "PI_HEXA.COMMAND", "POSITIONS MOVE_STATE MOVE_SEP INIT STOP_ERROR"
    )
    CHANNELS = {
        "X": "mm",
        "Y": "mm",
        "Z": "mm",
        "U": "deg",
        "V": "deg",
        "W": "deg",
    }
    MODELS = [850, 887]

    def __init__(self, *args, **kwargs):
        Controller.__init__(self, *args, **kwargs)

        self._cnx = None
        self._commands = dict()

        self.__model = None
        self.__version = None

    def initialize(self):
        """
        Initialize the communication to the hexapod controller
        """
        # velocity and acceleration are not mandatory in config
        self.axis_settings.config_setting["velocity"] = False
        self.axis_settings.config_setting["acceleration"] = False

        comm_type = get_comm_type(self.config.config_dict)
        comm_option = {"timeout": 3.0}
        if comm_type == TCP:
            comm_option["ctype"] = TCP
            comm_option.setdefault("port", 50000)
            self.__model = self.config.get("model", int, 887)
        elif comm_type == SERIAL:
            comm_option.setdefault("baudrate", 57600)
            comm_option["ctype"] = SERIAL
            self.__model = self.config.get("model", int, 850)
        else:
            raise ValueError(
                "PI_HEXA communication of type [%s] is not supported" % comm_type
            )

        if self.__model not in self.MODELS:
            raise ValueError(
                "PI_HEXA model %r is not supported, "
                "supported models are %r" % (self.__model, self.MODELS)
            )

        self._cnx = get_comm(self.config.config_dict, **comm_option)

        global_map.register(self, children_list=[self._cnx])

        commands = {
            850: {
                self.COMMAND.POSITIONS: "POS?",
                self.COMMAND.MOVE_STATE: ("\5", lambda x: int(x)),
                self.COMMAND.MOVE_SEP: "",
                self.COMMAND.INIT: "INI X",
                self.COMMAND.STOP_ERROR: 2,
            },
            887: {
                self.COMMAND.POSITIONS: "\3",
                self.COMMAND.MOVE_STATE: ("\5", lambda x: int(x, 16)),
                self.COMMAND.MOVE_SEP: " ",
                self.COMMAND.INIT: "FRF X",
                self.COMMAND.STOP_ERROR: 10,
            },
        }

        self._commands = commands[self.__model]

    def initialize_hardware(self):
        self.__version = self.command("*IDN?")

    def finalize(self):
        if self._cnx is not None:
            self._cnx.close()

    def initialize_axis(self, axis):
        channel = axis.config.get("channel", str)
        if channel not in self.CHANNELS:
            raise ValueError(
                "PI_HEXA: wrong channel %s on axis %s, "
                "should be one of %r" % (channel, axis.name, list(self.CHANNELS.keys()))
            )
        axis.channel = channel
        axis._unit = axis.config.get("unit", str, self.CHANNELS[channel])

    @property
    def controller_model(self):
        return self.__model

    @property
    def controller_version(self):
        return self.__version

    def __info__(self):
        info = f"PI HEXAPODE MODEL {self.__model}:\n"
        info += f"\t{self.__version}\n"
        info += f"\tCOMM: {self._cnx}"
        return info

    def get_axis_info(self, axis):
        info = f"AXIS:\n\tchannel: {axis.channel}"
        return info

    def read_position(self, axis):
        return self._read_all_positions()[axis.channel]

    @_atomic_communication
    def state(self, axis):
        cmd, test_func = self._commands[self.COMMAND.MOVE_STATE]
        moving_flag = test_func(self.command(cmd, 1))
        if moving_flag:
            self._check_error_and_raise()
            return AxisState("MOVING")
        return AxisState("READY")

    def home_state(self, axis):
        # home_search is blocking until the end,
        # so this is called when homing is done;
        # at the end of axis homing, all axes
        # have changed position => do a sync hard
        try:
            return self.state(axis)
        finally:
            for axis in self.axes.values():
                axis.sync_hard()

    def start_one(self, motion):
        self.start_all(motion)

    @_atomic_communication
    def start_all(self, *motions):
        self.clear_errors()
        sep = self._commands[self.COMMAND.MOVE_SEP]
        cmd = "MOV " + " ".join(
            [
                "%s%s%g" % (motion.axis.channel, sep, motion.target_pos)
                for motion in motions
            ]
        )
        self.command(cmd)
        self._check_error_and_raise()

    def stop(self, axis):
        self.stop_all()

    @_atomic_communication
    def stop_all(self, *motions):
        self.command("STP")
        self._check_error_and_raise(ignore_stop=True)

    def command(self, cmd, nb_line=None, **kwargs):
        """
        Send raw command to the controller
        """
        cmd = cmd.strip()
        need_reply = cmd.find("?") > -1 if nb_line is None else nb_line
        cmd += "\n"
        cmd = cmd.encode()
        if need_reply:
            if nb_line is not None and nb_line > 1:
                return [
                    r.decode()
                    for r in self._cnx.write_readlines(cmd, nb_line, **kwargs)
                ]
            else:
                return self._cnx.write_readline(cmd, **kwargs).decode()
        else:
            return self._cnx.write(cmd)

    @_atomic_communication
    def home_search(self, axis, switch):
        self.clear_errors()
        init_cmd = self._commands[self.COMMAND.INIT]
        self.command(init_cmd)
        self._check_error_and_raise(timeout=30.0)

    def _read_all_positions(self):
        cmd = self._commands[self.COMMAND.POSITIONS]
        answer = self.command(cmd, nb_line=6)
        positions = self._parse_all_channels(answer, "positions")
        return positions

    def _parse_all_channels(self, answer, msg="channels"):
        result = dict()
        try:
            for chan, ans in zip(self.CHANNELS, answer):
                if not ans.startswith(f"{chan}="):
                    raise RuntimeError(f"PI_HEXA error parsing {msg} answer")
                result[chan] = float(ans[len(chan) + 1 :])
        except ValueError:
            self._cnx.flush()
            raise
        return result

    def _check_error_and_raise(self, ignore_stop=False, **kwargs):
        err = int(self.command("ERR?", **kwargs))
        if err > 0:
            if (
                ignore_stop and err == self._commands[self.COMMAND.STOP_ERROR]
            ):  # stopped by user
                return
            human_error = get_error_str(err)
            errors = [self.name, err, human_error.replace("\n", "")]
            raise RuntimeError("PI_HEXA {0} [err={1}] {2}".format(*errors))

    def clear_errors(self):
        err = 1
        att = 0
        while att < 10 and err > 0:
            err = int(self.command("ERR?"))
            att += 1

    def read_rotation_origin(self):
        answer = self.command("SPI?", 3)
        origin = dict()
        for ans in answer:
            (chan, val) = ans.split("=")
            origin[chan] = float(val)
        return origin

    def write_rotation_origin(self, origin_dict):
        sep = self._commands[self.COMMAND.MOVE_SEP]
        cmd = "SPI " + " ".join(
            ["%s%s%g" % (chan, sep, pos) for (chan, pos) in origin_dict.items()]
        )
        self.command(cmd)
        self._check_error_and_raise()

    @object_method(types_info=("None", "dict"))
    def get_rotation_origin(self, axis):
        return self.read_rotation_origin()

    @object_method(types_info=("dict", "None"))
    def set_rotation_origin(self, axis, origin_dict):
        self.write_rotation_origin(origin_dict)

    def read_all_hw_limits(self):
        answer = self.command("NLM?", 6)
        lowlim = self._parse_all_channels(answer, "negative limits")
        answer = self.command("PLM?", 6)
        highlim = self._parse_all_channels(answer, "positive limits")
        reslim = dict()
        for (chan, low), high in zip(lowlim.items(), highlim.values()):
            reslim[chan] = (low, high)
        return reslim

    @object_method(types_info=("None", ("float", "float")))
    def get_hw_limits(self, axis):
        limits = self.read_all_hw_limits()
        return limits[axis.channel]

    def print_command_help(self):
        ans = self.command("HLP?")
        print(ans)
        while True:
            try:
                ans = self._cnx.readline(timeout=0.1)
                print(ans.decode())
            except CommunicationTimeout:
                break
