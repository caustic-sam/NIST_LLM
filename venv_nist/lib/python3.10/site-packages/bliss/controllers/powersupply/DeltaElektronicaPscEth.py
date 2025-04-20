from functools import partial

from bliss.common.utils import autocomplete_property

from bliss.comm.util import get_comm
from bliss.comm.scpi import (
    SCPI,
    COMMANDS,
    Commands,
    Cmd,
    BoolCmd,
    StrCmd,
    FloatCmdRO,
    FloatCmd,
)

from bliss.controllers.bliss_controller import BlissController
from bliss.common.counter import SamplingCounter  # noqa: F401
from bliss.controllers.counter import SamplingCounterController


def __decodeErr(s):
    code, desc = map(str.strip, s.split(",", 1))
    return dict(code=int(code), desc=desc)


LocalErrCmd = partial(Cmd, get=__decodeErr)

DEPSCETH_COMMANDS = Commands(
    COMMANDS,
    {
        # -- system commands
        "SYSTem:REMote[:STATus]": StrCmd(doc="remote programming status"),
        "SYSTem:REMote:CV[:STATus]": StrCmd(doc="remote voltage programming"),
        "SYSTem:REMote:CC[:STATus]": StrCmd(doc="remote current programming"),
        "SYSTem:ERRor[:NEXT]": LocalErrCmd(doc="error code and message"),
        # -- enable power
        "OUTPut": BoolCmd(doc="enable output"),
        # -- configuration commands
        "SOURce:VOLTage:MAXimum": FloatCmd(doc="maximum voltage"),
        "SOURce:CURRent:MAXimum": FloatCmd(doc="maximum current"),
        "SOURce:VOLTage": FloatCmd(doc="set output voltage"),
        "SOURce:CURRent": FloatCmd(doc="set output current"),
        # -- measurement commands
        "MEASure:VOLTage": FloatCmdRO(doc="measured output voltage"),
        "MEASure:CURRent": FloatCmdRO(doc="measured output current"),
        # "MEASure:POWer": FloatCmdRO(doc="measured output power"),
        # only on firmware > 4.3.0 : compute it instead
    },
)


class DeltaElektronicaPscEthSCC(SamplingCounterController):
    def __init__(self, name, device):
        super().__init__(name)
        self.device = device

    def read(self, counter):
        return getattr(self.device, counter.tag)


class DeltaElektronicaPscEth(BlissController):
    def __init__(self, config):
        super().__init__(config)
        self.__comm = get_comm(self.config)
        self.__scpi = SCPI(self.__comm, commands=DEPSCETH_COMMANDS)

        self._scc = DeltaElektronicaPscEthSCC(self.name, self)

    def _get_subitem_default_class_name(self, cfg, parent_key):
        if parent_key == "counters":
            return "SamplingCounter"

    def _create_subitem_from_config(
        self, name, cfg, parent_key, item_class, item_obj=None
    ):
        if parent_key == "counters":
            units = {"voltage": "V", "current": "A", "power": "W"}
            mode = cfg.get("mode", "SINGLE")
            tag = cfg["tag"]
            unit = units.get(tag, None)
            obj = self._scc.create_counter(item_class, name, mode=mode, unit=unit)
            obj.tag = tag
            return obj

    def _load_config(self):
        for cfg in self.config["counters"]:
            self._get_subitem(cfg["name"])

    def _init(self):
        self.__idn = self.__scpi["*IDN"]
        self.__max_voltage = self.__scpi["SOUR:VOLT:MAX"]
        self.__max_current = self.__scpi["SOUR:CURR:MAX"]

    @autocomplete_property
    def counters(self):
        return self._scc.counters

    @property
    def comm(self):
        return self.__comm

    @property
    def scpi(self):
        return self.__scpi

    @property
    def idn(self):
        return self.__idn

    def __info__(self):
        idn = self.idn
        volt = self.voltage
        curr = self.current
        power = volt * curr

        info = f"PowerSupply {idn['manufacturer']} - {idn['model']}\n"
        info += f"Voltage : {volt:.3f} V\t[setpoint {self.voltage_setpoint:.3f} V]\n"
        info += f"Current : {curr:.3f} A\t[setpoint {self.current_setpoint:.3f} A]\n"
        info += f"Power   : {power:.3f} W\n"
        info += f"Output  : {self.output}\n"
        info += (
            f"Control : voltage={self.voltage_control} current={self.current_control}\n"
        )
        return info

    @property
    def power(self):
        return self.voltage * self.current

    @property
    def voltage(self):
        return self.__scpi["MEAS:VOLT"]

    @voltage.setter
    def voltage(self, value):
        self.voltage_setpoint = value

    @property
    def voltage_setpoint(self):
        return self.__scpi["SOUR:VOLT"]

    @voltage_setpoint.setter
    def voltage_setpoint(self, value):
        if value < 0.0 or value > self.__max_voltage:
            raise ValueError(f"Voltage range is [0, {self.__max_voltage}]")
        self.__scpi["SOUR:VOLT"] = value

    @property
    def voltage_maximum(self):
        return self.__max_voltage

    @property
    def voltage_control(self):
        value = self.__scpi["SYST:REM:CV"]
        return value == "REM" and "ON" or "OFF"

    @voltage_control.setter
    def voltage_control(self, value):
        if isinstance(value, str):
            bval = value.upper() == "ON"
        else:
            bval = bool(value)
        setval = bval and "REM" or "LOC"
        self.__scpi["SYST:REM:CV"] = setval

    @property
    def current(self):
        return self.__scpi["MEAS:CURR"]

    @current.setter
    def current(self, value):
        self.current_setpoint = value

    @property
    def current_setpoint(self):
        return self.__scpi["SOUR:CURR"]

    @current_setpoint.setter
    def current_setpoint(self, value):
        if value < 0.0 or value > self.__max_current:
            raise ValueError(f"Current range is [0, {self.__max_current}]")
        self.__scpi["SOUR:CURR"] = value

    @property
    def current_maximum(self):
        return self.__max_current

    @property
    def current_control(self):
        value = self.__scpi["SYST:REM:CC"]
        return value == "REM" and "ON" or "OFF"

    @current_control.setter
    def current_control(self, value):
        if isinstance(value, str):
            bval = value.upper() == "ON"
        else:
            bval = bool(value)
        setval = bval and "REM" or "LOC"
        self.__scpi["SYST:REM:CC"] = setval

    @property
    def output(self):
        value = self.__scpi["OUTP"]
        return value and "ON" or "OFF"

    @output.setter
    def output(self, value):
        self.__scpi["OUTP"] = value

    def on(self):
        self.output = "ON"
        self.voltage_control = "ON"
        self.current_control = "ON"

    def off(self):
        self.output = "OFF"
        self.voltage_control = "OFF"
        self.current_control = "OFF"
