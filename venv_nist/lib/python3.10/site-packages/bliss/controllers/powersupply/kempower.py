import gevent
import numbers

from bliss.common.counter import SamplingCounter
from bliss.controllers.counter import SamplingCounterController
from bliss.common.protocols import CounterContainer
from bliss.common.utils import autocomplete_property, typecheck
from bliss.comm.util import get_comm
from bliss.shell.getval import getval_yes_no

"""
Configuration example

- class: Kempower
  module: powersupply.kempower
  plugin: bliss
  name: kem
  timeout: 10
  serial:
    url: ser2net://lid312:28000/dev/ttyRP27
    baudrate: 4800
  counters:
    - name: kemv
      tag: voltage
      mode: SINGLE
    - name: kemc
      tag: current
      mode: SINGLE
"""


class KempowerCounterController(SamplingCounterController):
    TAGS = ["voltage", "current", "mag"]
    UNITS = {"voltage": "%", "current": "%", "mag": "T"}

    def __init__(self, device, config):
        super().__init__(f"{device.name}")
        self.device = device

        cnts_config = config.get("counters")
        if cnts_config is not None:
            for conf in cnts_config:
                name = conf["counter_name"].strip()
                tag = conf["tag"].strip().lower()
                if tag not in self.TAGS:
                    raise ValueError("KempowerCounterController: invalid tag")
                mode = conf.get("mode", "SINGLE")
                unit = self.UNITS[tag]
                cnt = self.create_counter(SamplingCounter, name, unit=unit, mode=mode)
                cnt.tag = tag

    def read(self, cnt):
        if cnt.tag == "voltage":
            return self.device.voltage_setpoint
        elif cnt.tag == "current":
            return self.device.current_setpoint
        elif cnt.tag == "mag":
            return self.device.T_setpoint


class Kempower(CounterContainer):
    def __init__(self, name, config):
        self.__name = name
        self.__config = config
        self.__voltage_sp = 0  # we cannot read voltage!
        self.__current_sp = 0  # we cannot read current!
        self.__T_sp = 0
        self._step = config.get("step", 10)
        self._sleep1 = config.get("sleep1", 1)
        self._sleep2 = config.get("sleep2", 2)

        self.__comm = None

        self.__cc = KempowerCounterController(self, config)

    @property
    def name(self):
        return self.__name

    @property
    def config(self):
        return self.__config

    @property
    def comm(self):
        if self.__comm is None:
            self.__comm = get_comm(self.config)
        return self.__comm

    @autocomplete_property
    def counters(self):
        return self.__cc.counters

    def __info__(self):
        return f"Kempower power supply {self.comm}"

    @property
    def step(self):
        """
        Return the step value used to ramp to desired current
        """
        return self._step

    @property
    def sleep1(self):
        """
        Return the sleep time used to stabilize current while ramping
        """
        return self._sleep1

    @property
    def sleep2(self):
        """
        Return the sleep time used before returning to user
        """
        return self._sleep2

    def on(self):
        if not getval_yes_no(
            "Please check voltage and current are set to 0 - do you want to continue ?"
        ):
            return
        self.comm.write(bytes([255, 3, 253, 0]))
        gevent.sleep(self.sleep2)

    def off(self):
        self.current_setpoint = 0
        self.voltage_setpoint = 0
        self.comm.write(bytes([255, 3, 239, 0]))
        gevent.sleep(3 * self.sleep2)

    @property
    def voltage_setpoint(self):
        return self.__voltage_sp

    @voltage_setpoint.setter
    @typecheck
    def voltage_setpoint(self, value: numbers.Real):
        """Set voltage on controller

        value: % of maximum power, from 0 to 100
        """
        val = int(abs(value * 1023 / 100))
        self.comm.write(bytes([255, 1, val % 256, val // 256]))
        self.__voltage_sp = value
        print(f"Voltage set to {self.voltage_setpoint} %")
        gevent.sleep(self.sleep2)

    @property
    def current_setpoint(self):
        return self.__current_sp / 10.23

    @current_setpoint.setter
    @typecheck
    def current_setpoint(self, value: numbers.Real):
        """current as a pourcentage (0% to 83%)"""
        if value > 83:
            raise ValueError("Value exceeds maximum (80%)")
        value *= 1023 / 100
        if value > self.__current_sp:
            sign = 1
        elif value < self.__current_sp:
            sign = -1
        else:
            sign = 0
        next_sp = self.__current_sp
        while sign:
            if sign == 1:
                next_sp += self.step
                if next_sp > value:
                    next_sp = value
                    sign = 0
            elif sign == -1:
                next_sp -= self.step
                if next_sp < value:
                    next_sp = value
                    sign = 0
            self.__current_sp = next_sp
            val = int(abs(next_sp))
            v1 = val % 256
            v2 = val // 256
            if next_sp < 0:
                v2 |= 0x10
            self.comm.write(bytes([255, 61, v1, v2]))
            gevent.sleep(self.sleep1)
        print(f"Current set to {self.current_setpoint} %")
        gevent.sleep(self.sleep2)

    @property
    def T_setpoint(self):
        return self.__T_sp

    @T_setpoint.setter
    @typecheck
    def T_setpoint(self, value: numbers.Real):
        if value < 0 or value > 1:
            raise ValueError("Invalid value for magnetic field (negative or >1 T)")

        self.__T_sp = value
        # set voltage to 100%
        self.voltage_setpoint = 100
        # formula to convert value in T to current
        self.current_setpoint = (value / 0.004564) * 100 / 264
