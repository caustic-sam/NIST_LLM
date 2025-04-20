"""Implements macro motors and control macros for COMET XRP X generator

yml configuration example:

- class: Comet
  module: xgen.comet
  plugin: bliss
  name: Comet
  serial:
    url: tango://id00/com1/lxrlab1
    baudrate: 9600
  counters:
    - name: xr_comet_cv
      tag: voltage
    - name: xr_comet_ci
      tag: current
"""
import re
from enum import Enum

from bliss.common.counter import SamplingCounter
from bliss.controllers.counter import SamplingCounterController
from bliss.common.protocols import CounterContainer
from bliss.common.soft_axis import SoftAxis
from bliss.common.axis.state import AxisState

from bliss import global_map
from bliss.common.logtools import log_debug
from bliss.common.utils import autocomplete_property

from bliss.comm.exceptions import CommunicationError
from bliss.comm.util import get_comm


class CometCounterController(SamplingCounterController):
    TAGS = ["voltage", "current"]
    UNITS = {"voltage": "0.1Kv", "current": "0.01A"}

    def __init__(self, device, config):
        super().__init__(f"{device.name}")
        self.device = device

        cnts_config = config.get("counters")
        if cnts_config is not None:
            for conf in cnts_config:
                name = conf["name"].strip()
                tag = conf["tag"].strip().lower()
                if tag not in self.TAGS:
                    raise ValueError("CometCounterController: invalid tag")
                mode = conf.get("mode", "SINGLE")
                unit = self.UNITS[tag]
                cnt = self.create_counter(SamplingCounter, name, unit=unit, mode=mode)
                cnt.tag = tag

    def read(self, cnt):
        return getattr(self.device, cnt.tag)


class Status(Enum):
    standby = b"STAND BY"
    prewarming = b"PREWARNING TIME"
    high_tension_on = b"HIGH TENSION ON"
    ramping = b"ACTUAL VALUE UNEQUAL SET VALUE"
    set = b"ACTUAL VALUE EQUAL SET VALUE"
    post_heating = b"POSTHEATING TIME"
    ready = b"X-RAY ON VIA SERIAL INTERFACE POSSIBLE"


class Focus(Enum):
    standard = b"F0"
    small = b"F1"


class Comet(CounterContainer):
    __STATUS = b"Z"
    __PARAMS = b"L"
    __REMOTE_ON = b"\002"  # STX to start command mode
    __REMOTE_OFF = b"\003"  # ETX to stop command mode
    __HT_ON = b"ON"
    __HT_OFF = b"OF"

    __DELAY = 0.05  # Not so fast, you monkeys

    def __init__(self, name, config):
        self.__name = name
        self.__config = config
        self.__serial = get_comm(config)
        self.__serial.write(self.__REMOTE_OFF)
        self.__serial.flush()

        # Counters
        self.__cc = CometCounterController(self, config)

        # Axes
        def axis_state():
            s = self.status
            if Status.high_tension_on not in s:
                res = AxisState("DISABLED")
            elif Status.set in s:
                res = AxisState("READY")
            elif Status.ramping in s:
                res = AxisState("MOVING")
            else:
                res = AxisState("FAULT")

            return res

        self.__soft_axes = [
            SoftAxis(
                "comet_mv",
                self,
                position="voltage",
                move="voltage_setpoint",
                state=axis_state,
            ),
            SoftAxis(
                "comet_mc",
                self,
                position="current",
                move="current_setpoint",
                state=axis_state,
            ),
        ]

        # Compile regex for parsing answer from control unit
        self.__re_parse = re.compile(rb"^\s(\d+),(\d)")

        # Register the counter container
        global_map.register(self, children_list=[self.__serial])

    @property
    def name(self):
        return self.__name

    @property
    def comm(self):
        return self.__serial

    @autocomplete_property
    def counters(self):
        return self.__cc.counters

    def __info__(self):
        info = "COMET XRP Xray generator\n"
        info += f"Communication : {self.__serial}\n\n"
        info += f"Voltage : {self.voltage:.3f} kV [setpoint {self.voltage_setpoint:.3f} kV]\n"
        info += f"Current : {self.current:.3f} mA [setpoint {self.current_setpoint:.3f} mA]\n"
        return info

    def __xrp_parse(self, ans: bytes):
        log_debug(self, "Parsing answer %s", ans)

        # Examples of expected answers "U 015,2" or "US 199,0"
        ans = self.__re_parse.subn(rb"\1.\2", ans)
        if ans[1] == 0:
            raise CommunicationError(f"Unexpected answer from COMET device, got {ans}")

        log_debug(self, "Replaces answer '%s'", ans[0])

        return float(ans[0])

    def __xrp_query(self, query: bytes, size=6):
        log_debug(self, "Sending query %s", query)
        return self.__serial.write_read(
            self.__REMOTE_ON + query + b"\r" + self.__REMOTE_OFF,
            size=size,
            timeout=Comet.__DELAY,
        )

    def __xrp_cmd(self, cmd: bytes):
        log_debug(self, "Sending command %s", cmd)
        self.__serial._write(self.__REMOTE_ON + cmd + b"\r" + self.__REMOTE_OFF)

    @property
    def time(self):
        """Returns the value of the automatic HV switching off time [seconds]"""
        ans = self.__xrp_query(b"T")
        if ans == b" XX:XX":
            ans = "HV switching off time DISABLED"
        else:
            ans = self.__xrp_parse(ans)

        return ans

    @property
    def hvoff_time(self):
        """Returns the value of the automatic HV switching off time [seconds]"""
        ans = self.__xrp_query(b"TS")
        if ans == b" XX:XX":
            ans = "HV switching off time DISABLED"
        else:
            ans = self.__xrp_parse(ans)

        return ans

    @hvoff_time.setter
    def hvoff_time(self, seconds: int):
        """Set the automatic HV switching off time [seconds]"""

        if seconds < 2:
            raise ValueError("The minium delta is 2 seconds")

        mins = int(seconds / 60)
        secs = int(seconds % 60)
        self.__xrp_cmd(bytes(f"T{mins:02d}{secs:02d}\r", encoding="ascii"))
        return

    def hvoff_time_disable(self):
        """Disable the automatic HV switching off time"""
        self.__xrp_cmd(b"T9999")
        return

    @staticmethod
    def __decode_status(status: bytes):
        res = int(status)
        if res == 1:
            res = Status.standby
        elif res == 2:
            res = Status.prewarming
        elif res == 3:
            res = Status.high_tension_on
        elif res == 4:
            res = Status.ramping
        elif res == 5:
            res = Status.set
        elif res == 6:
            res = Status.post_heating
        elif res == 8:
            res = Status.ready
        else:
            raise CommunicationError(
                f"Unexpected status from COMET device, got {status}"
            )
        return res

    @property
    def status(self):
        """Return a list of current status [STAND BY, PREWARNING TIME, HIGH TENSION ON, POSTHEATING TIME...]"""
        ans = self.__xrp_query(self.__STATUS, size=1)
        res = [Comet.__decode_status(ans)]

        # If status is high_tension_on, read an additional status
        if res[0] == Status.high_tension_on:
            ans = self.__serial.read(size=1, timeout=1.0)
            res.append(Comet.__decode_status(ans))

        return res

    @property
    def system(self):
        """Return the system information in hexa"""
        ans = self.__xrp_query(b"S", size=5)
        # TODO Decode this register
        return b"Ox" + ans.strip()

    @property
    def mode(self):
        """Return the mode selected"""
        ans = self.__xrp_query(b"M", size=4)
        return int(ans)

    @property
    def focus(self):
        """Returns the current focal spot [standard, small]"""
        ans = self.__xrp_query(b"F")
        if ans == b" FOC #":
            return Focus.standard
        elif ans == b" foc .":
            return Focus.small
        else:
            raise CommunicationError(f"Unexpected answer from COMET device, got {ans}")

    @focus.setter
    def focus(self, focus: Focus):
        """Set the focal spot [standard, small]"""
        self.__xrp_cmd(focus.value)
        # TODO Check answer?

    @property
    # @units(result=ur.kV)
    def voltage(self):
        """Returns the voltage [Kv]"""
        ans = self.__xrp_query(b"U")
        return self.__xrp_parse(ans)  # * ur.kV

    @property
    # @units(result=ur.kV)
    def voltage_setpoint(self):
        """Returns the voltage setpoint [Kv]"""
        ans = self.__xrp_query(b"US")
        return self.__xrp_parse(ans)  # * ur.kV

    @voltage_setpoint.setter
    # @units(result=ur.kV)
    def voltage_setpoint(self, value: float):
        """Set the voltage setpoint [Kv]"""
        value = int(value * 10)  # in 0.1Kv
        self.__xrp_cmd(bytes(f"U{value:04d}", encoding="ascii"))

    @property
    # @units(result=ur.mA)
    def current(self):
        """Returns the current [mA]"""
        ans = self.__xrp_query(b"I")
        return self.__xrp_parse(ans)  # * ur.mA

    @property
    # @units(result=ur.mA)
    def current_setpoint(self):
        """Returns the current setpoint [mA]"""
        ans = self.__xrp_query(b"IS")
        return self.__xrp_parse(ans)  # * ur.mA

    @current_setpoint.setter
    # @units(result=ur.mA)
    def current_setpoint(self, value: float):
        """Set  the current setpoint [mA]"""
        value = int(value * 100)  # in 0.01mA
        self.__xrp_cmd(bytes(f"I{value:04d}", encoding="ascii"))

    @property
    def hv(self):
        """Return True is the H.T. is currently ON"""
        return self.status == Status.high_tension_on

    @hv.setter
    def hv(self, on_off: bool):
        """Swith the H.T. On/Off"""
        if on_off:
            # ans = self.__xrp_query(self.__HT_ON, size=1)
            self.__xrp_cmd(self.__HT_ON)

            # TODO Wait for "HIGH TENSION ON" ?
        else:
            self.__xrp_cmd(self.__HT_OFF)
