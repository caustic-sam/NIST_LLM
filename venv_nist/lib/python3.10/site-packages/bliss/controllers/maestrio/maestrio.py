# -*- coding: utf-8 -*-
#
# Copyright (c) 2022 Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import re
import enum
import functools
import weakref
import struct
import numpy
import typing
import hashlib
from ast import literal_eval
from datetime import datetime

from bliss.comm.tcp import Command
from bliss.common.utils import autocomplete_property
from bliss.common.protocols import CounterContainer
from bliss.config.conductor.client import remote_open
from bliss import global_map
from bliss.config.settings import HashSetting

from .counters import MaestrioCounterController


class MaestrioError(Exception):
    def __init__(self, maestrio, message):
        self.msg = f"device [{maestrio.name}] {message}"
        super().__init__(self, self.msg)

    def __str__(self):
        return self.msg


def debug(funct):
    def f(*args, **kwargs):
        print(args, kwargs)
        values = funct(*args, **kwargs)
        print("return", values)
        return values

    return f


def _get_simple_property(command_name, doc_sring):
    def get(self):
        return self.putget("?%s" % command_name)

    def set(self, value):
        return self.putget("%s %s" % (command_name, value))

    return property(get, set, doc=doc_sring)


def _simple_cmd(command_name, doc_sring):
    def exec_cmd(self):
        return self.putget(command_name)

    return property(exec_cmd, doc=doc_sring)


CHANNEL_MODE = enum.Enum(
    "CHANNEL_MODE", "QUAD PULSE " "SSI BISS HSSL ENDAT ADC VOLT UNUSED"
)
INPUT_MODE = enum.Enum("INPUT_MODE", "TTL TTL50  NIM VOLT")
OUTPUT_MODE = enum.Enum("OUTPUT_MODE", "OFF NIM TTL FREQ VOLT")
SMART_ACC_MODE = enum.Enum(
    "SMART_ACC_MODE", "UNDEF COUNT_UP COUNT_UPDOWN INTEGR READ SAMPLE TRACK"
)
PROGRAM_STATE = enum.Enum("PROGRAM_STATE", "NOPROG BAD UNLOAD STOP IDLE RUN BREAK")


class MaestrioSequencerState(typing.NamedTuple):
    seq_id: int
    name: str
    state: PROGRAM_STATE
    resource: list


class MaestrioProgramLoadedState(typing.NamedTuple):
    name: str
    checksum: int
    timestamp: float


def _activate_switch(func):
    @functools.wraps(func)
    def f(self, *args, **kwargs):
        if self._switch is not None and self._switch_name is not None:
            self._switch.set(self._switch_name)
        return func(self, *args, **kwargs)

    return f


def lazy_init(func):
    @functools.wraps(func)
    def f(self, *args, **kwargs):
        self._init()
        return func(self, *args, **kwargs)

    return f


# Base class for Inputs Outputs and Channels.
class MaestrioBaseInputOutput:
    def __init__(self, maestrio, channel_id, switch=None, switch_name=None):
        self._maestrio = weakref.ref(maestrio)
        self._channel_id = channel_id
        self._mode = None
        if switch is not None:
            # check if has the good interface
            if switch_name is None:
                if not hasattr(switch, "get"):
                    raise MaestrioError(
                        self._maestrio(),
                        f"{self.__class__.__name__} ({channel_id}), "
                        "switch object doesn't have a set method",
                    )
            if not hasattr(switch, "set"):
                raise MaestrioError(
                    self._maestrio(),
                    f"{self.__class__.__name__} ({channel_id}), "
                    "switch object doesn't have a set method",
                )
            self._switch = switch
            self._switch_name = switch_name
        else:
            self._switch = None
            self._switch_name = None

    @property
    @_activate_switch
    def channel_id(self):
        return self._channel_id

    @property
    def switch(self):
        return self._switch

    @property
    def switch_name(self):
        if self._switch and self._switch_name is None:
            return self._switch.get()
        return self._switch_name

    @property
    @_activate_switch
    def value(self):
        return "NO VALUE"

    @property
    def mode(self):
        if self._mode is None:
            self._mode = self._read_config_mode()
        return self._mode

    def _read_config_mode(self):
        return None


class MaestrioChannel(MaestrioBaseInputOutput):

    CONVERSION_PARS = ["steps_per_unit", "offset", "sign", "modulo", "unit"]

    def __init__(
        self,
        maestrio,
        channel_id,
        channel_config=None,
        switch=None,
        switch_name=None,
    ):
        super().__init__(maestrio, channel_id, switch=switch, switch_name=switch_name)
        self._encoder_conversion_param = None
        if channel_config is not None:
            conv = dict()
            for param in self.CONVERSION_PARS:
                value = channel_config.get(param)
                if value is not None:
                    conv[param] = value
            if len(conv):
                self._encoder_conversion_param = conv

    @property
    def encoder_conversion_param(self):
        return self._encoder_conversion_param

    @property
    def mode_str(self):
        smode = str(self.mode)
        return smode.replace("CHANNEL_MODE.", "")

    @property
    @_activate_switch
    def value(self):
        maestrio = self._maestrio()
        string_value = maestrio.putget("?CHVAL C%d" % self._channel_id)
        return self._convert(string_value)

    @value.setter
    @_activate_switch
    def value(self, val):
        maestrio = self._maestrio()
        maestrio.putget("CHVAL C%d POS %s" % (self._channel_id, val))

    @property
    @_activate_switch
    def raw_value(self):
        maestrio = self._maestrio()
        string_value = maestrio.putget("?CHVAL C%d RAWPOS" % self._channel_id)
        return self._convert(string_value)

    @property
    @_activate_switch
    def offset(self):
        maestrio = self._maestrio()
        string_value = maestrio.putget("?CHVAL C%d OFFSET" % self._channel_id)
        return self._convert(string_value)

    @offset.setter
    @_activate_switch
    def offset(self, value):
        maestrio = self._maestrio()
        maestrio.putget("?CHVAL C%d OFFSET %s" % (self._channel_id, value))

    def _convert(self, string_value):
        """Return channel value, converted according to the configured mode."""
        return string_value  # TODO????

    def _read_config_mode(self):
        """Read configuration of the current channel from MAESTRIO board to
        determine the usage mode of the channel.
        """
        maestrio = self._maestrio()
        string_config = maestrio.putget("?CHCFG C%d TYPE" % self._channel_id)
        split_config = string_config.split()
        mode = CHANNEL_MODE[split_config[1]]
        return mode

    @_activate_switch
    def sync(self, value=0):
        maestrio = self._maestrio()
        if self.mode in (CHANNEL_MODE.QUAD, CHANNEL_MODE.PULSE):
            # set both channel value and accumulator
            maestrio.putget("CHVAL C%d POS %s" % (self._channel_id, value))
            maestrio.putget(
                "SACCCFG CH%d TRACK SRC C%d" % (self._channel_id, self._channel_id)
            )
            maestrio.putget("SACCCFG CH%d TRACK INIT %d" % (self._channel_id, value))
        else:
            # set accumulator in read mode (absolute encoder)
            maestrio.putget(
                "SACCCFG CH%d READ SRC C%d" % (self._channel_id, self._channel_id)
            )

    def __info__(self):
        info = f"Channel #{self._channel_id}\n"
        fill = "Config :"
        for line in self.get_config():
            info += f"{fill} {line}\n"
            fill = " " * 8
        if self._switch:
            name = self._switch.name
            info += f"Switch : {self.switch_name} on [{name}]\n"
        info += f"Value  : {self.value}\n"
        if self._encoder_conversion_param:
            info += "Encoder conversion :\n"
            for (name, value) in self._encoder_conversion_param.items():
                info += f"  {name:<15s} = {value}\n"
        return info

    def get_config(self):
        maestrio = self._maestrio()
        chan = f"C{self._channel_id:d}"
        cfg = list()
        cfg.append("CHCFG " + maestrio.putget(f"?CHCFG {chan}"))
        mode = self.mode
        if mode == CHANNEL_MODE.QUAD:
            cfg.append("QUADCFG " + maestrio.putget(f"?QUADCFG {chan}"))
        elif mode == CHANNEL_MODE.PULSE:
            cfg.append("PULSECFG " + maestrio.putget(f"?PULSECFG {chan}"))
        elif mode == CHANNEL_MODE.SSI:
            cfg.append("SSICFG " + maestrio.putget(f"?SSICFG {chan}"))
        elif mode == CHANNEL_MODE.BISS:
            cfg.append("BISSCFG " + maestrio.putget(f"?BISSCFG {chan}"))
        elif mode == CHANNEL_MODE.ENDAT:
            cfg.append("ENDATCFG " + maestrio.putget(f"?ENDATCFG {chan}"))
        elif mode == CHANNEL_MODE.HSSL:
            cfg.append("HSSLCFG " + maestrio.putget(f"?HSSLCFG {chan}"))
        elif mode == CHANNEL_MODE.VOLT:
            cfg.append("VOLTCFG " + maestrio.putget(f"?VOLTCFG {chan}"))
        return cfg


class MaestrioInput(MaestrioBaseInputOutput):
    def __init__(self, maestrio, channel_id, switch=None, switch_name=None):
        super().__init__(maestrio, channel_id, switch=switch, switch_name=switch_name)

    @property
    def value(self):
        maestrio = self._maestrio()
        string_value = maestrio.putget("?INVAL I%d" % self._channel_id)
        return self._convert(string_value)

    def _convert(self, string_value):
        return string_value

    def _read_config_mode(self):
        """Read configuration of the current init from MAESTRIO board to
        determine the usage mode of the input.
        """
        maestrio = self._maestrio()
        string_config = maestrio.putget("?INCFG I%d" % self._channel_id)
        split_config = string_config.split()
        mode = INPUT_MODE[split_config[1]]
        return mode

    def __info__(self):
        info = f"Input #{self._channel_id}\n"
        info += "Config : "
        info += self.get_config() + "\n"
        info += f"Value  : {self.value}\n"
        return info

    def get_config(self):
        maestrio = self._maestrio()
        cfg = maestrio.putget("?INCFG I%d" % self._channel_id)
        return f"INCFG {cfg}"


class MaestrioOutput(MaestrioBaseInputOutput):
    def __init__(self, maestrio, channel_id, switch=None, switch_name=None):
        super().__init__(maestrio, channel_id, switch=switch, switch_name=switch_name)

    @property
    def value(self):
        maestrio = self._maestrio()
        string_value = maestrio.putget("?OUTVAL O%d" % self._channel_id)
        return self._convert(string_value)

    @value.setter
    def value(self, value):
        maestrio = self._maestrio()
        maestrio.putget("OUTVAL O%d %s" % (self._channel_id, value))

    def _convert(self, string_value):
        return string_value

    def _read_config_mode(self):
        """Read configuration of the current init from MAESTRIO board to
        determine the usage mode of the output.
        """
        maestrio = self._maestrio()
        string_config = maestrio.putget("?OUTCFG I%d" % self._channel_id)
        split_config = string_config.split()
        mode = OUTPUT_MODE[split_config[1]]
        return mode

    def __info__(self):
        info = f"Output #{self._channel_id}\n"
        info += "Config : "
        info += self.get_config() + "\n"
        info += f"Value  : {self.value}\n"
        return info

    def get_config(self):
        maestrio = self._maestrio()
        cfg = maestrio.putget("?OUTCFG O%d" % self._channel_id)
        return f"OUTCFG {cfg}"


class Maestrio(CounterContainer):
    BINARY_SIGNATURE = 0xA5A50000
    BINARY_NOCHECKSUM = 0x00000010
    PROGRAM_STATE = PROGRAM_STATE
    CHANNEL_MODE = CHANNEL_MODE
    INPUT_MODE = INPUT_MODE
    OUTPUT_MODE = OUTPUT_MODE
    SMART_ACC_MODE = SMART_ACC_MODE
    CHANNEL_RANGE = range(1, 7)
    INPUT_RANGE = range(1, 21)
    OUTPUT_RANGE = range(1, 9)
    SEQ_RANGE = range(1, 4)

    APPNAME = _simple_cmd("?APPNAME", "Return application name")
    VERSION = _simple_cmd("?VERSION", "Return application version")
    HELP = _simple_cmd("?HELP", "Return list of commands")
    HELP_ALL = _simple_cmd("?HELP ALL", "Return all list of commands")

    def __init__(self, name, config):
        self._name = name
        hostname = config.get("host")
        self._cnx = Command(hostname, 5000, eol="\n")
        global_map.register(self, children_list=[self._cnx])

        self._config = config
        self._channels = None
        self._named_channels = None
        self._counters_container = self._get_sampling_counter_controller(config)

        self._var_info_cache = dict()
        self._init_prog_defaults()
        self._init_prog_settings()

    def _init_prog_defaults(self):
        self._prog_defaults = dict()
        for prog_config in self._config.get("program_defaults", []):
            prog_name = prog_config.get("program_name")
            if prog_name is None:
                raise MaestrioError(
                    self, "missing program_name in program_defaults configuration"
                )
            vals = self._prog_defaults.setdefault(prog_name.upper(), dict())
            seq_id = prog_config.get("sequencer_id")
            if seq_id is not None and int(seq_id) in self.SEQ_RANGE:
                vals["seq_id"] = int(seq_id)
            seq_res = prog_config.get("sequencer_resources")
            if seq_res is not None:
                vals["seq_res"] = seq_res.split(" ")

    def _init_prog_settings(self):
        name = self._name + "_programs"
        self._prog_settings = HashSetting(name)
        proglist = self.get_program_loaded_state()
        prognames = [prog.name for prog in proglist]

        # remove settings for prog not on maestrio
        for name in self._prog_settings.keys():
            if name not in prognames:
                self._prog_settings.remove(name)

        # update settings with prog seen on maestrio
        # use checksum computed on maestrio
        for prog in proglist:
            try:
                checksum = self.get_program_source(prog.name, "CHECKSUM")
            except RuntimeError:
                # regurlarly fails on init. skip it.
                self._set_prog_settings(prog.name, 0, None)
                continue
            if prog.checksum != checksum:
                self._set_prog_settings(prog.name, checksum, None)

    def _set_prog_settings(self, name, checksum, timestamp):
        self._prog_settings[name.upper()] = checksum, timestamp

    def _get_prog_settings(self, name):
        state = self._prog_settings.get(name.upper())
        if state is not None:
            return literal_eval(state)
        else:
            return (None, None)

    @property
    def name(self):
        return self._name

    def _init(self):
        if self._named_channels is None:
            self._init_channels(self._config)

    def _get_sampling_counter_controller(self, config_tree):
        """
        This method can be defined in sub-classes and return a specific
        Counter controller
        """
        return MaestrioCounterController(self, config_tree)

    def _init_channels(self, config):
        self._named_channels = dict()
        self._channels = dict()

        chan_list = config.get("channels", list())
        for chan_config in chan_list:
            chan = chan_config.get("channel")
            if chan is None:
                raise MaestrioError(self, "missing channel key in config")
            if not chan.startswith("CH"):
                continue
            chan_number = int(chan[2:])
            if chan_number not in self.CHANNEL_RANGE:
                raise MaestrioError(self, f"wrong channel number in {chan} config")
            chan_switch = chan_config.get("switch")
            chan_label = chan_config.get("label")
            if chan_switch is not None:
                if not hasattr(chan_switch, "states_list"):
                    raise MaestrioError(self, f"wrong switch object in {chan} config")
                for chan_label in chan_switch.states_list():
                    named_channels = self._named_channels.setdefault(
                        chan_label.upper(), list()
                    )
                    named_channels.append(
                        MaestrioChannel(
                            self,
                            chan_number,
                            switch=chan_switch,
                            switch_name=chan_label,
                        )
                    )
                self._channels[chan_number] = MaestrioChannel(
                    self, chan_number, switch=chan_switch
                )
            elif chan_label is not None:
                channel = MaestrioChannel(self, chan_number, channel_config=chan_config)
                named_channels = self._named_channels.setdefault(
                    chan_label.upper(), list()
                )
                named_channels.append(channel)
                self._channels[chan_number] = channel
            else:
                channel = MaestrioChannel(self, chan_number, channel_config=chan_config)
                self._channels[chan_number] = channel

    # @debug
    def putget(self, cmd, data=None):
        """Raw connection to the Maestrio card.

        cmd -- the message you want to send
        data -- binnary or ascii data
        """
        _check_reply = re.compile(r"^[#?]")
        reply_flag = _check_reply.match(cmd)
        cmd_raw = cmd.encode()
        cmd_name = cmd_raw.split()[0].strip(b" #").upper()
        if data is not None:
            # check if string; i.e program
            if isinstance(data, str) or isinstance(data, bytes):
                if isinstance(data, str):
                    raw_data = data.encode()
                else:
                    raw_data = data

                header = struct.pack(
                    "<III",
                    self.BINARY_SIGNATURE | self.BINARY_NOCHECKSUM | 0x1,
                    len(raw_data),
                    0x0,
                )  # no checksum
                full_cmd = b"%s\n%s%s" % (cmd_raw, header, raw_data)
                transaction = self._cnx._write(full_cmd)
            elif isinstance(data, numpy.ndarray):
                if data.dtype in [
                    numpy.uint8,
                    numpy.int8,
                    numpy.uint16,
                    numpy.int16,
                    numpy.uint32,
                    numpy.int32,
                    numpy.uint64,
                    numpy.int64,
                ]:
                    raw_data = data.tostring()
                    header = struct.pack(
                        "<III",
                        self.BINARY_SIGNATURE
                        | self.BINARY_NOCHECKSUM
                        | data.dtype.itemsize,
                        int(len(raw_data) / data.dtype.itemsize),
                        0x0,
                    )  # no checksum
                    full_cmd = b"%s\n%s%s" % (cmd_raw, header, raw_data)
                    transaction = self._cnx._write(full_cmd)
                else:
                    raise RuntimeError(f"Numpy datatype {data.dtype} not yet managed")
            else:
                raise RuntimeError("Not implemented yet ;)")
        else:
            transaction = self._cnx._write(cmd_raw + b"\n")
        with self._cnx.Transaction(self._cnx, transaction):
            if reply_flag:
                msg = self._cnx._readline(
                    transaction=transaction, clear_transaction=False
                )
                if not msg.startswith(cmd_name):
                    raise RuntimeError(
                        f"Unknown error, send {cmd}, reply:", msg.decode()
                    )
                if msg.find(b"ERROR") > -1:
                    # patch to workaround protocol issue in maestrio
                    # in Error, maestrio send two '\n' instead of one
                    if not transaction.empty() and transaction.peek()[0:1] == b"\n":
                        data = transaction.get()[1:]
                        if len(data):
                            transaction.put(data)
                    raise RuntimeError(msg.decode())
                elif msg.find(b"$") > -1:  # multi line
                    msg = self._cnx._readline(
                        transaction=transaction, clear_transaction=False, eol=b"$\n"
                    )
                    return msg.decode()
                elif msg.find(b"?*") > -1:  # binary reply
                    header_size = struct.calcsize("<III")
                    header = self._cnx._read(
                        transaction, size=header_size, clear_transaction=False
                    )
                    magic_n_type, size, check_sum = struct.unpack("<III", header)
                    magic = magic_n_type & 0xFFFF0000
                    assert magic == self.BINARY_SIGNATURE
                    raw_type = magic_n_type & 0xF
                    numpy_type = {1: numpy.uint8, 2: numpy.uint16, 4: numpy.uint32}.get(
                        raw_type
                    )
                    data = self._cnx._read(
                        transaction,
                        size=size * numpy_type().dtype.itemsize,
                        clear_transaction=False,
                    )
                    return numpy.frombuffer(data, dtype=numpy_type)
                return msg[len(cmd_name) :].strip(b" ").decode()

    def __info__(self):
        info = ""
        info += f"MAESTRIO board: {self.APPNAME} - {self.VERSION}\n"
        info += f"{self._cnx.__info__()}\n"
        info += "CHANNELS:\n"
        for chan_id in range(1, 7):
            chan = self.get_channel(chan_id)
            mode = chan.mode_str
            value = chan.value
            info += f"    C{chan_id} : {value:>10} - {mode}\n"
        info += "PROGRAMS UPLOADED:\n"
        for prog in self.get_program_loaded_state():
            info += f"    {prog.name:<12} : "
            if prog.timestamp:
                date = datetime.fromtimestamp(prog.timestamp)
                date = date.strftime("%y/%m/%d %H:%M:%S")
                info += f"uploaded on {date}\n"
            else:
                info += "external upload\n"
        info += "SEQUENCERS:\n"
        for seq in self.get_sequencer_state():
            name = str(seq.name)
            state = str(seq.state).replace("PROGRAM_STATE.", "")
            res = " ".join(seq.resource)
            info += f"    SEQ{seq.seq_id} : {state:>8} {name:<10} - RES: {res}\n"
        return info

    def get_channel_config(self):
        config_str = ""
        for chan_id in self.CHANNEL_RANGE:
            chan = self.get_channel(chan_id)
            cfg = chan.get_config()
            config_str += "\n".join(cfg) + "\n"
        return config_str

    def dump_channel_config(self):
        print(self.get_channel_config())

    def get_channel(self, channel_id, config=None, switch=None, switch_name=None):
        if channel_id in self.CHANNEL_RANGE:
            return self._channels.get(channel_id, MaestrioChannel(self, channel_id))
        else:
            raise MaestrioError(self, "doesn't have channel id %d" % channel_id)

    @lazy_init
    def get_channel_by_name(self, channel_name):
        """<channel_name>: Label of the channel."""
        channel_name = channel_name.upper()
        channels = self._named_channels.get(channel_name)
        if channels is None:
            raise MaestrioError(
                self, "doesn't have channel (%s) in his config" % channel_name
            )
        return channels[0]  # first match

    @lazy_init
    def get_channel_by_names(self, *channel_names):
        """<channel_names>: Labels of the channels."""
        channels = dict()
        for channel_name in channel_names:
            chans = self._named_channels.get(channel_name.upper())
            if chans is None:
                raise MaestrioError(
                    self, "doesn't have channel (%s) in his config" % channel_name
                )
            else:
                for chan in chans:
                    if chan.channel_id not in channels:
                        channels[chan.channel_id] = chan
                        break
                else:
                    raise MaestrioError(
                        self, "Can't find all names on distinct channel"
                    )
        return list(channels.values())

    @lazy_init
    def get_channel_names(self):
        return list(self._named_channels.keys())

    def get_input(self, channel_id, switch=None, switch_name=None):
        if channel_id in self.INPUT_RANGE:
            return MaestrioInput(
                self, channel_id, switch=switch, switch_name=switch_name
            )
        else:
            raise MaestrioError(self, "doesn't have input id %d" % channel_id)

    def get_input_config(self):
        config_str = ""
        for chan_id in self.INPUT_RANGE:
            chan = self.get_input(chan_id)
            config_str += chan.get_config()
            config_str += "\n"
        return config_str

    def dump_input_config(self):
        print(self.get_input_config())

    def get_output(self, channel_id, switch=None, switch_name=None):
        if channel_id in self.OUTPUT_RANGE:
            return MaestrioOutput(
                self, channel_id, switch=switch, switch_name=switch_name
            )
        else:
            raise MaestrioError(self, "doesn't have output id %d" % channel_id)

    def get_output_config(self):
        config_str = ""
        for chan_id in self.OUTPUT_RANGE:
            chan = self.get_output(chan_id)
            config_str += chan.get_config()
            config_str += "\n"
        return config_str

    def dump_output_config(self):
        print(self.get_output_config())

    def dump_trigger_config(self):
        print(self.putget("?DICFG"))

    def dump_config(self):
        print("CHANNELS configuration :")
        print("------------------------")
        self.dump_channel_config()
        print("INPUTS configuration :")
        print("----------------------")
        self.dump_input_config()
        print("OUTPUTS configuration :")
        print("-----------------------")
        self.dump_output_config()
        print("TRIGGER IN configuration :")
        print("--------------------------")
        self.dump_trigger_config()

    # program functions

    def get_program_loaded_state(self):
        proglist = self.get_program_list()
        result = list()
        for name in proglist:
            (checksum, timestamp) = self._get_prog_settings(name)
            result.append(MaestrioProgramLoadedState(name, checksum, timestamp))
        return result

    def get_program_list(self, include_internal=False):
        """
        get program loaded on flash memory
        """
        rawlist = self.putget("?PRGPROG")
        plist = rawlist.splitlines()
        if include_internal:
            return plist
        return [x for x in plist if not x.startswith("#")]

    def upload_program_file(self, progname, filename, force=False):
        with remote_open(filename) as program:
            progsrc = program.read()

        self.upload_program(progname, progsrc, force)

    def _compute_program_checksum(self, src):
        if isinstance(src, str):
            raw_src = src.encode()
        else:
            raw_src = src
        m = hashlib.md5()
        m.update(raw_src)
        checksum = m.hexdigest()
        return checksum

    def upload_program(self, name, src, force=False):
        """
        Upload a program into flash memory
        name -- programe name
        src -- programe source
        """
        checksum = self._compute_program_checksum(src)
        if not force:
            (last_checksum, last_timestamp) = self._get_prog_settings(name)
            if checksum == last_checksum:
                return

        self._var_info_cache[name.upper()] = None
        self.putget(f"#*PRGPROG {name}", data=src)
        self._set_prog_settings(name, checksum, datetime.now().timestamp())

    def erase_program(self, name):
        """
        Erase a program in flash memory
        """
        if self._var_info_cache.get(name.upper()):
            self._var_info_cache[name.upper()] = None
        self.putget(f"#PRGCLEAR {name}")
        self._prog_settings.remove(name.upper())

    def get_sequencer_state(self, seq_id=None):
        if seq_id is None:
            result = list()
            state = self.putget("?PRGSTATE")
            for line in state.splitlines():
                (seq_id, state, name) = self.__extract_prog_state(line)
                res = self.putget(f"?PRGRES SEQ{seq_id}")
                result.append(
                    MaestrioSequencerState(seq_id, name, state, res.splitlines())
                )
            return result
        else:
            line = self.putget(f"?PRGSTATE SEQ{seq_id}")
            (seq_id, state, name) = self.__extract_prog_state(line)
            res = self.putget(f"?PRGRES SEQ{seq_id}")
            return MaestrioSequencerState(seq_id, name, state, res.splitlines())

    def __extract_prog_state(self, line):
        seq_exp = re.compile("SEQ([0-9]+)")

        params = line.split()
        g = seq_exp.match(params[0])
        if g:
            seq_nb = int(g.group(1))
            prog_state = PROGRAM_STATE[params[1]]
            try:
                prog_name = params[2]
            except IndexError:
                prog_name = None
            return seq_nb, prog_state, prog_name
        else:
            raise MaestrioError(self, f"Parsing prog state line {line}")

    def get_program_state(self, program_name=None):
        """
        Get the current state of the program
        """
        if program_name is None:  # get all status
            return_dict = {}
            status = self.putget("?PRGSTATE")
            for line in status.splitlines():
                seq_nb, prog_state, prog_name = self.__extract_prog_state(line)
                if prog_name is not None:
                    return_dict[prog_name] = prog_state
            return return_dict
        else:
            line = self.putget(f"?PRGSTATE {program_name}")
            return PROGRAM_STATE[line]

    def is_program_running(self, program_name):
        state = self.get_program_state(program_name)
        return state == PROGRAM_STATE.RUN

    def get_sequencer_resource(self, seq_id=None):
        allseq = self.get_sequencer_state()
        if seq_id:
            for seq in allseq:
                if seq.seq_id == seq_id:
                    return seq.resource
            return None
        else:
            result = dict()
            for seq in allseq:
                result[seq.seq_id] = seq.resource
            return result

    def set_sequencer_resource(self, seq_id, *res):
        for idx in range(1, 4):
            if idx != seq_id:
                self.release_sequencer_resource(idx, *res)
        res_str = " ".join(res)
        self.putget(f"#PRGRES SEQ{seq_id} {res_str}")

    def release_sequencer_resource(self, seq_id, *res):
        res_str = " ".join(res)
        self.putget(f"#PRGRES SEQ{seq_id} RELEASE {res_str}")

    def get_program_sequencer(self, program_name=None):
        allseq = self.get_sequencer_state()
        if program_name:
            for seq in allseq:
                if seq.name == program_name.upper():
                    return seq.seq_id
            return None
        else:
            result = dict()
            for seq in allseq:
                if seq.name is not None:
                    result[seq.name] = seq.seq_id
            return result

    def get_program_source(self, program_name, code_type=""):
        """
        get the source of a program name uploaded in the flash memory.
        """
        return self.putget(f"?PRGPROG {program_name} {code_type}")

    def get_program_var_info(self, program_name):
        """
        get variable information from a program name.
        """
        vars_info = self._var_info_cache.get(program_name.upper())
        if vars_info is not None:
            return vars_info

        vars_info = {}
        reply = self.putget(f"?PRGVARINFO {program_name}")
        if reply.startswith("no variables available"):
            raise RuntimeError("No variables found. Is program loaded?")
        match_var_info = re.compile(r"^([A-z0-0_]+) +([0-9]+) +(\w+)( .+)?$")
        for line in reply.split("\n"):
            g = match_var_info.match(line)
            if g is None:
                continue
            vars_info[g.group(1)] = (
                int(g.group(2)),
                numpy.uint32 if g.group(3) == "UNSIGNED" else numpy.int32,
            )
        self._var_info_cache[program_name.upper()] = vars_info
        return vars_info

    def get_program_var_values(self, program_name, *var_names):
        """
        get current values of program variables.
        program_name -- the program name
        *var_names -- can restrict to a list of variables names. if empty all

        return a dictionary with VARIABLE_NAME:VARIABLE_VALUE
        """
        vars_values = {}
        vars_info = self.get_program_var_info(program_name)
        if not var_names:
            var_names = set(vars_info.keys())
        else:
            var_names = set(x.upper() for x in var_names)

        var_array_names = var_names.intersection(
            set(x for x, y in vars_info.items() if y[0] > 1)
        )
        var_scalar_names = var_names - var_array_names
        # get all scalars
        # todo... for now, not possible to do it in one go :-(
        for vname in var_scalar_names:
            vars_values[vname] = int(self.putget(f"?PRGVAR {program_name} {vname}"))
        # Now array
        for vname in var_array_names:
            reply = self.putget(f"?*PRGVAR {program_name} {vname}")
            nb, dtype = vars_info[vname]
            vars_values[vname] = reply.astype(dtype)
        return vars_values

    def set_program_var_values(self, program_name, **var_values):
        """
        Set var value to the <program_name>
        var_values is a dictionary where key is a name of
        the var and value is the value of the mac.
        ie: VARTIME=range(100) or NB=10
        """
        var_info = self.get_program_var_info(program_name)
        for vname, value in var_values.items():
            vname = vname.upper()
            nb, dtype = var_info[vname]
            if nb > 1:  # array send in binnary
                data = numpy.array(value, dtype=dtype)
                self.putget(f"#*PRGVAR {program_name} {vname}", data=data)
            else:
                self.putget(f"#PRGVAR {program_name} {vname} {value}")

    def get_program_var_array_values(
        self, program_name, var_name, start_range=0, last_range=-1
    ):
        """
        get var array with a specific target range.
        i.e: change values for range 5 to 10 of VARTIME
             var_name=VARTIME, values=[1,2,3,4,5],start_range=5,last_range=9
        """
        var_arr_value = {}
        var_info = self.get_program_var_info(program_name)
        nb, dtype = var_info[var_name]
        if nb <= 1:
            raise RuntimeError(
                f"Variable {var_name} in program {program_name} is not an array"
            )

        if start_range == 0 and last_range == -1:
            reply = self.putget(f"?*PRGVAR {program_name} {var_name}")
        else:
            if last_range >= 0:
                reply = self.putget(
                    f"?*PRGVAR {program_name} {var_name}[{start_range}:{last_range}]",
                )
            else:
                reply = self.putget(
                    f"?*PRGVAR {program_name} {var_name}[{start_range}:]"
                )
        var_arr_value[var_name] = reply.astype(dtype)
        return var_arr_value

    def set_program_var_array_values(
        self, program_name, var_name, values, start_range=0, last_range=-1
    ):
        """
        set var array with a specific target range.
        i.e: change values for range 5 to 10 of VARTIME
             var_name=VARTIME, values=[1,2,3,4,5],start_range=5,last_range=9
        """
        var_info = self.get_program_var_info(program_name)
        nb, dtype = var_info[var_name]
        if nb <= 1:
            raise RuntimeError(
                f"Variable {var_name} in program {program_name} is not an array"
            )

        data = numpy.array(values, dtype=dtype)
        if start_range == 0 and last_range == -1:
            self.putget(f"#*PRGVAR {program_name} {var_name}", data=data)
        else:
            if last_range >= 0:
                self.putget(
                    f"#*PRGVAR {program_name} {var_name}[{start_range}:{last_range}]",
                    data=data,
                )
            else:
                self.putget(
                    f"#*PRGVAR {program_name} {var_name}[{start_range}:]", data=data
                )

    def get_program_mac_values(self, program_name, *macro_names):
        """
        get current value of the macros defined in **program_name**
        program_name -- the program name
        *macro_names -- can restrict to a list of macros names. if empty all

        return a dictionary with MACRONAME:MACROVALUE
        """
        macs = {}
        mac_names_string = " ".join(macro_names)
        for line in self.putget(f"?PRGMAC {program_name} {mac_names_string}").split(
            "\n"
        ):
            if not line:
                continue
            pos = line.find(":=")
            mac_name, value = line[:pos].strip(), line[pos + 2 :].strip()
            macs[mac_name] = value
        return macs

    def set_program_mac_values(self, program_name, **mac_variables):
        """
        Set macro value to the <program_name>
        mac_variables is a dictionary where key is a name of
        the mac and value is the value of the mac.
        ie: NFRAMES=100
        """
        for name, value in mac_variables.items():
            self.putget(f"#PRGMAC {program_name} {name} := {value}")

    @lazy_init
    def load_program(self, prog_name, seq_id=None, seq_res=None):
        """
        Load a program from the flash memory to a sequencer.
        prog_name -- program name (need to be upload first)
        seq_id -- sequencer id (by default load an the first one)
        """
        defaults = self._prog_defaults.get(prog_name.upper(), {})
        if seq_id is None:
            seq_id = defaults.get("seq_id", 1)
        else:
            seq_id = int(seq_id)
        if seq_res is None:
            seq_res = defaults.get(
                "seq_res",
                [
                    "ALL",
                ],
            )

        self.putget(f"#PRGLOAD SEQ{seq_id} {prog_name}")
        self.set_sequencer_resource(seq_id, *seq_res)

    @lazy_init
    def unload_program(self, prog_name, seq_id=None):
        """
        Unload a program from a sequencer.
        prog_name -- the program name
        seq_id -- sequencer id (optional)
        """
        if seq_id is not None:
            self.putget(f"#PRGUNLOAD SEQ{seq_id} {prog_name}")
        else:
            self.putget(f"#PRGUNLOAD {prog_name}")

    @lazy_init
    def run_program(self, name, seq_id=None):
        """
        Run a program
        """
        if seq_id is None:
            seq_id = self.get_program_sequencer(name)
            if seq_id is None:
                raise MaestrioError(
                    self,
                    "Program not loaded, give sequencer id or use load_program first",
                )
            self.putget(f"#PRGRUN {name}")
        else:
            self.putget(f"#PRGRUN SEQ{seq_id} {name}")

    @lazy_init
    def stop_program(self, name, seq_id=None):
        """
        Stop a program
        """
        if seq_id is None:
            self.putget(f"#PRGSTOP {name}")
        else:
            self.putget(f"#PRGSTOP SEQ{seq_id} {name}")

    @lazy_init
    def get_data(self, nb_counters=None):
        """
        Download all available data.
        nb_counters -- number of counters defined in the STORELIST
        """

        # Check that samples are available, otherwise, this generate an error and flood the maestrio logs
        nsamples = int(self.putget("?DAQNSAMPL"))
        if nsamples == 0:
            return None

        data = self.putget("?*DAQDATA")
        if nb_counters is not None:
            data.shape = -1, nb_counters
        #        data.shape = nsamples, -1

        return data

    @autocomplete_property
    @lazy_init
    def counters(self):
        return self._counters_container.counters

    @lazy_init
    def _read_smart_acc_config(self):
        smart_config = self.putget("?SACCCFG")
        exp = re.compile(
            r"^(CH[1-6]|IN[0-9]+) +(COUNT +(?:UP|UPDOWN)|INTEGR|READ|SAMPLE|TRACK) +"
            "SRC +(I[0-9]+|C[1-6])"
        )

        smart_acc_cfg = {}
        for line in smart_config.split("\n"):
            if not line:
                continue

            g = exp.match(line)
            if g is not None:
                channel_name = g.group(1)
                channel_mode_name = g.group(2).replace(" ", "_")
                try:
                    channel_mode = SMART_ACC_MODE[channel_mode_name]
                except KeyError:
                    raise KeyError(
                        "Can't find smart accumulator type {channel_type_name}"
                    )
                smart_acc_cfg[channel_name] = {
                    "mode": channel_mode,
                    "source": g.group(3),
                }
            else:
                not_defined = re.compile(r"^(CH[1-6]|IN[0-9]+) +not configured")
                g = not_defined.match(line)
                if g is None:
                    # Try old compatibility to be removed in future.
                    old_exp = re.compile(r"^SACC([0-9]+)")
                    g = old_exp.match(line)
                    if g is not None:
                        channel_id = int(g.group(1))
                        if channel_id <= 20:
                            smart_acc_cfg["IN%d" % channel_id] = {
                                "mode": SMART_ACC_MODE.COUNT_UP,
                                "source": "I%d" % channel_id,
                            }
                        else:
                            channel_id -= 20
                            smart_acc_cfg["CH%d" % channel_id] = {
                                "mode": SMART_ACC_MODE.UNDEF,
                                "source": "C%d" % channel_id,
                            }

                        continue
                    raise RuntimeError(
                        f"Cannot parse smart accumulator config line: {line}"
                    )

                channel_name = g.group(1)
                smart_acc_cfg[channel_name] = {
                    "mode": SMART_ACC_MODE.UNDEF,
                    "source": None,
                }
        return smart_acc_cfg
