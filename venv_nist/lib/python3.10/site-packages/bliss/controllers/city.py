from bliss import global_map
from bliss.comm.util import get_comm
from bliss.common.greenlet_utils import protect_from_kill
from bliss.common.utils import RED

"""

- plugin: generic
  class: City
  name: city
  tcp:
    url: cityid241:5000

  # declare used chammels
  channels:
    - channel: OA5
    - channel: OA6
    - channel: OB1
    - channel: OB2
    - channel: OB4
    - channel: OB5
    - channel: OB6
"""


class City:
    def __init__(self, config):
        self._name = config.get("name")
        self._config = config

        # Constants
        self._output_channel_names = [
            "OA1",
            "OA2",
            "OA3",
            "OA4",
            "OA5",
            "OA6",
            "OB1",
            "OB2",
            "OB3",
            "OB4",
            "OB5",
            "OB6",
            "OC1",
            "OC2",
            "OC3",
            "OC4",
            "OC5",
            "OC6",
            "OD1",
            "OD2",
            "OD3",
            "OD4",
            "OD5",
            "OD6",
        ]
        self._software_trig_names = ["SW1", "SW2", "SW3", "SW4"]
        self._fine_delay_channel_names = ["OA1", "OA2", "OA3", "OA4", "OA5", "OA6"]

        # Communication
        self._cnx = get_comm(config, timeout=3)
        global_map.register(self, children_list=[self._cnx])

        # YML parsing
        self._channels = {}
        ch_config = config.get("channels", None)
        for ch in ch_config:
            name = ch.get("channel")
            if name in self._output_channel_names:
                self._channels[name] = City2Channel(name, self)
                setattr(self, name, self._channels[name])
            else:
                print(RED(f"City2: Channel {name} does not exist"))

    def __info__(self):
        info_str = "CITY\n\n"
        info_str += f"Name:   {self._name}\n"
        info_str += f"Host:   {self._cnx._host}\n"
        info_str += f"Socket: {self._cnx._port}\n\n"
        for name, obj in self._channels.items():
            info_str += f"{obj._get_info()}\n"
        return info_str

    def help(self):
        ret = self._comm("?HELP")
        print(ret)

    def monitor(self):
        ret = self._comm("?MONITOR")
        print(ret)

    def state(self):
        ret = self._comm("?STATE")
        print(ret)

    def _channel_state(self):
        self._channel_state_arr = {}
        ret = self.command("?CHEN").split("\n")
        for aux in ret:
            arr = aux.split(" ")
            if arr[0] == "CHCFG":
                self._channel_state_arr[arr[1]] = arr[2]
            else:
                self._channel_state_arr[arr[0]] = "NOT CONFIGURED"

    def sw_pulse(self, channel):
        # channel = [SW1 | SW2 | SW3 | SW4]
        if channel in self._software_trig_names:
            self.command(f"#TRGSOFT {channel}")

    def ch_sync(self, channels=None):
        if channels is None:
            chan = " ".join(self._channels.keys())
        else:
            chan = " ".join(channels)
        self.command(f"#CHSYNC {chan}")

    @property
    def rflock(self):
        return self._comm("?RFLOCK")

    def version(self):
        print(self._comm("?VERSION"))

    """
    Ethernet Communication
    """

    def command(self, cmd):
        return self._comm(cmd)

    def _comm_ack(self, msg):
        return self.comm("#" + msg)

    @protect_from_kill
    def _comm(self, cmd, timeout=None, text=True):
        self._cnx.open()
        with self._cnx._lock:
            self._cnx._write((cmd + "\r\n").encode())
            if cmd.startswith("?") or cmd.startswith("#"):
                msg = self._cnx._readline(timeout=timeout)
                cmd = cmd.strip("#").split(" ")[0]
                msg = msg.replace((cmd + " ").encode(), "".encode())
                if msg.startswith("$".encode()):
                    msg = self._cnx._readline(
                        # transaction=transaction,
                        # clear_transaction=False,
                        eol="$\n",
                        timeout=timeout,
                    )
                    return msg.strip("$\n".encode()).decode()
                if msg.startswith("ERROR".encode()):
                    raise RuntimeError(msg.decode())
                if text:
                    return (msg.strip("\r\n".encode())).decode()
                return msg.strip("\r\n".encode())


class City2Channel:
    def __init__(self, name, controller):
        self._name = name
        self._controller = controller

    def __info__(self):
        return self._get_info()

    def _get_info(self):
        self._controller._channel_state()
        mystr = f"    Channel        : {self._name} {self._controller._channel_state_arr[self._name]}\n"
        mystr += f"    Current Config : {self.current_config()}\n"
        return mystr

    def _command(self, cmd):
        return self._controller.command(cmd)

    def enable(self):
        self._command(f"#CHCFG {self._name} ON")

    def disable(self):
        self._command(f"#CHCFG {self._name} OFF")

    def state(self):
        self._controller._channel_state()
        return self._controller._channel_state_arr[self._name]

    """
    SOURCE
    """

    def configure_source(self, source, value):
        # source: DIVIDER | EXTERNAL
        current_config = self.current_config()
        arr = current_config.split()
        cmd = f"#CHCFG {arr[0]} {arr[1]}"
        if source == "DIVIDER":
            cmd += f" SOURCE DIVIDER {value}"
        elif source == "EXTERNAL":
            cmd += " SOURCE EXTERNAL"
        else:
            raise ValueError("source musst be [DIVIDER|EXTERNAL]")
        try:
            next_ind = arr.index("NETWORK")
        except Exception:
            try:
                next_ind = arr.index("LOCAL")
            except Exception:
                raise RuntimeError("Cannot get channel configuration")
        for ind in range(next_ind, len(arr)):
            cmd += f" {arr[ind]}"
        self._command(cmd)

    def configure_source_sync(self, sync_mode, sync_channel, active, wait_channel):
        # sync_mode: LOCAL | NETWORK
        # sync_channel:
        #       LOCAL: DI1:4 | SW1:4 | OA1:6 | ... | OD1:6
        #       NETWORK: OC | EVR01 | ... | EVR12
        # active: NEXT | WAIT
        # wait_channel: DI1:4 | SW1:4
        current_config = self.current_config()
        arr = current_config.split()
        try:
            start_ind = arr.index("LOCAL")
        except Exception:
            try:
                start_ind = arr.index("NETWORK")
            except Exception:
                raise RuntimeError("Cannot get channel configuration")
        cmd = "#CHCFG"
        for ind in range(start_ind):
            cmd += f" {arr[ind]}"
        cmd += f" {sync_mode} {sync_channel} ACTIVE {active}"
        if active == "WAIT":
            cmd += f" {wait_channel}"
        for ind in range(arr.index("CDELAY"), len(arr)):
            cmd += f" {arr[ind]}"
        self._command(cmd)

    """
    BURST
    """

    def configure_burst(self, state, npulse, channel):
        # state = [ON | OFF]
        # npulse = Number of pulses sent
        # channel = [DI1:4 | SW1:4]

        current_config = self.current_config()
        arr = current_config.split()

        cmd = "#CHCFG"
        for ind in range(arr.index("BURST")):
            cmd += f" {arr[ind]}"

        if state == "ON":
            cmd += f" BURST ON {npulse} {channel}"
        else:
            cmd += " BURST OFF"

        for ind in range(arr.index("GATE"), len(arr)):
            cmd += f" {arr[ind]}"

        self._command(cmd)

    def burst_state(self):
        conf = self.current_config().split()
        state = conf[conf.index("BURST") + 1]
        if state == "ON":
            return int(conf[conf.index("BURST") + 2])
        else:
            return "OFF"

    def burst_off(self):
        self.configure_burst("OFF", 0, 0)

    def burst_on(self, npulse, src):
        self.configure_burst("ON", npulse, src)

    """
    GATE
    """

    def configure_gate(self, state, polarity, channel):
        # state = [ON | OFF]
        # polarity = [NORMAL | INVERTED]
        # channel = [DI1:4 | SW1:4]

        current_config = self.current_config()
        arr = current_config.split()

        cmd = "#CHCFG"
        for ind in range(arr.index("GATE")):
            cmd += f" {arr[ind]}"

        cmd += f" GATE {state} {polarity} {channel}"

        self._command(cmd)

    def gate_state(self):
        conf = self.current_config().split()
        return conf[-3]

    def gate_src(self):
        conf = self.current_config().split()
        return conf[-1]

    def gate_src_state(self):
        src = self.gate_src()
        if src in self._controller._software_trig_names:
            return self._command(f"?TRGSOFT {self.gate_src()}").split()[-1]
        else:
            return src

    """
    SIGNAL
    """

    def configure_signal(self, polarity, cdelay, fdelay, width):
        # rf_tick = 1/?RFFREQ = 1/ 352374776 = 2.83e-9 s
        # rfdelay_tick = 10 ps
        #
        # polarity = [NORMAL | INVERTED]
        # cdelay = <rf_ticks>
        # fdelay = <fdelay_ticks>
        # width = [50% | <rf_ticks>]

        current_config = self.current_config()
        arr = current_config.split()

        cmd = f"#CHCFG {arr[0]}"

        cmd += f" {polarity}"

        for ind in range(arr.index("SOURCE"), arr.index("CDELAY")):
            cmd += f" {arr[ind]}"

        cmd += f" CDELAY {cdelay}"
        if self._name in self._controller._fine_delay_channel_names:
            cmd += f" FDELAY {fdelay} WIDTH {width}"
        else:
            cmd += f" WIDTH {width}"

        for ind in range(arr.index("BURST"), len(arr)):
            cmd += f" {arr[ind]}"

        self._command(cmd)

    def current_config(self):
        return self._command(f"?CHCFG {self._name}")

    def _get_config_array(self):
        ret = self.current_config().split()

        config = {}
        if ret[3] == "DIVIDER":
            config["divider"] = int(ret[4])
        if ret[-9].rfind("%") == -1:
            config["width"] = int(ret[-9])
        else:
            config["width"] = ret[-9]

        if ret[-12] == "FDELAY":
            config["fdelay"] = int(ret[-11])
            config["cdelay"] = int(ret[-13])
        else:
            config["fdelay"] = 0
            config["cdelay"] = int(ret[-11])

        return config
