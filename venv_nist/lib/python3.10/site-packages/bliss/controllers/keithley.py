# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Keithley meters.

YAML_ configuration example:

.. code-block:: yaml

    plugin: keithley               # (1)
    name: k_ctrl_1                 # (2)
    model: 6485                    # (3)
    auto_zero: False               # (4)
    display: False                 # (5)
    zero_check: False              # (6)
    zero_correct: False            # (7)
    gpib:                          # (8)
      url: enet://gpibid31eh
      pad: 12
    sensors:                       # (10)
    - name: mondio                 # (11)
      address: 1                   # (12)
      nplc: 0.1                    # (13)
      auto_range: False            # (14)


#. plugin name (mandatory: keithley)
#. controller name (mandatory). Some controller settings are needed. To hook the
   settings to the controller we use the controller name. That is why it is
   mandatory
#. controller model (optional. default: discover by asking instrument *IDN*)
#. auto-zero enabled (optional, default: False)
#. display enabled (optional, default: True)
#. zero-check enabled (optional, default: False). Only for 6485!
#. zero-correct enabled (optional, default: False). Only for 6485!
#. controller URL (mandatory, valid: gpib, tcp, serial)

  #. gpib (mandatory: *url* and *pad*). See :class:~bliss.comm.gpib.Gpib for
     list of options
  #. serial (mandatory: *port*). See :class:~bliss.comm.serial.Serial for list
     of options
  #. tcp (mandatory: *url*). See :class:~bliss.comm.tcp.Tcp for list of options

#. list of sensors (mandatory)
#. sensor name (mandatory)
#. sensor address (mandatory). Valid values:

  #. model 6482: 1, 2
  #. model 6485: 1
  #. model 6487: 1
  #. model 2000: 1
  #. model 2410: 1
  #. model 2700: 1
  #. model 485: 1
  #. model 486: 1
  #. model 487: 1

#. sensor DC current NPLC (optional, default: 0.1)
#. sensor DC current auto-range (optional, default: False)


Some parameters (described below) are stored as settings. This means that the
static configuration described above serves as a *default configuration*.
The first time ever the system is brought to life it will read this
configuration and apply it to the settings. From now on, the keithley object
will rely on its settings. This is the same principle as it is applied on the
bliss axis velocity for example.

The following controller parameters are stored as settings: *auto_zero*,
*display*, (and *zero_check* and *zero_correct* only for 6485).

The following sensor parameters are stored as settings:
*nplc*, *auto_range* and *range*.

A demo is available from the command line:

.. code-block:: python

    python -m bliss.controllers.keithley <url> <pad>

Developer details:

.. code-block::

    READ? <=> INIT + FETCH?
    MEASURE[:<function>]? <=> CONF[:<function>] + READ?  == CONF[:<function>] + INIT + READ?
"""

from bliss import global_map, current_session
from bliss.config.beacon_object import BeaconObject
from bliss.config.settings import pipeline
from bliss.comm.util import get_interface, get_comm
from bliss.comm.scpi import Cmd as SCPICmd
from bliss.comm.scpi import Commands as SCPICommands
from bliss.comm.scpi import BaseSCPIDevice
from bliss.common.utils import autocomplete_property
from bliss.common.tango import DeviceProxy
from bliss.common.counter import SamplingCounter
from bliss.controllers.counter import SamplingCounterController, CounterContainer
from bliss.common.protocols import HasMetadataForScanExclusive
from bliss.config.settings import HashObjSetting
from bliss.common.logtools import log_warning
from bliss.common.soft_axis import SoftAxis
from bliss.common.axis.state import AxisState

from .keithley_scpi_mapping import COMMANDS as SCPI_COMMANDS
from .keithley_scpi_mapping import MODEL_COMMANDS as SCPI_MODEL_COMMANDS


class KeithleySCPI(BaseSCPIDevice):
    """Keithley instrument through SCPI language. Can be used with any Keithley
    SCPI capable device.

    Example usage::

        from bliss.comm.gpib import Gpib
        from bliss.controllers.keithley import KeithleySCPI

        gpib = Gpib('enet://gpibhost', pad=10)
        keithley = KeithleySCPI(gpib)

        print( keithley('*IDN?') )
        print( keithley['*IDN'] )
    """

    def __init__(self, *args, **kwargs):
        commands = SCPICommands(SCPI_COMMANDS)
        model = str(kwargs.pop("model"))
        commands.update(SCPI_MODEL_COMMANDS.get(model, {}))
        kwargs["commands"] = commands
        super().__init__(*args, **kwargs)


class BaseSensor(SamplingCounter, BeaconObject):
    MeasureFunctions = SCPICommands({"CURRent[:DC]": SCPICmd()})
    MeasureRanges = {
        "CURRent[:DC]": [2e-9, 20e-9, 200e-9, 2e-6, 20e-6, 200e-6, 2e-3, 20e-3]
    }
    name = BeaconObject.config_getter("name")
    address = BeaconObject.config_getter("address")

    def __init__(self, config, controller):
        BeaconObject.__init__(self, config)
        SamplingCounter.__init__(self, self.name, controller._counter_controller)
        self.__controller = controller
        self.__measure_range_cache = None
        self.__model = self.__controller.config["model"]

    @autocomplete_property
    def comm(self):
        return self.__controller._keithley_comm

    @autocomplete_property
    def controller(self):
        return self.__controller

    @property
    def index(self):
        return self.address - 1

    @property
    def model(self):
        return self.__model

    @BeaconObject.property(default="CURR:DC", priority=-1)
    def meas_func(self):
        func = self.comm["CONF"]
        func = func.replace('"', "")
        return self.MeasureFunctions[func]["max_command"]

    @meas_func.setter
    def meas_func(self, func):
        func = self.MeasureFunctions[func]["max_command"]
        self.comm("CONF:" + func)
        # remove range and auto_range in settings
        if not self._in_initialize_with_setting:
            with pipeline(self.settings):
                del self.settings["auto_range"]
                del self.settings["range"]
        return func

    @BeaconObject.property(default=0.1)
    def nplc(self):
        cmd = self._meas_func_sensor_cmd("NPLC")
        return self.comm[cmd]

    @nplc.setter
    def nplc(self, value):
        cmd = self._meas_func_sensor_cmd("NPLC")
        self.comm[cmd] = value

    @BeaconObject.property(priority=1)
    def auto_range(self):
        cmd = self._meas_func_sensor_cmd("RANG:AUTO")
        return self.comm[cmd]

    @auto_range.setter
    def auto_range(self, value):
        cmd = self._meas_func_sensor_cmd("RANG:AUTO")
        self.comm[cmd] = value
        if value:
            self.disable_setting("range")
        else:
            self.enable_setting("range")

    @property
    def possible_ranges(self):
        """
        Return the valid ranges for the current measure functions.
        """
        if self.__measure_range_cache is None:
            measure_ranges = {}
            for measure_name, ranges in self.MeasureRanges.items():
                cmd = SCPICommands({measure_name: SCPICmd()})
                cmd_info = next(iter(cmd.command_expressions.values()))
                full_name = cmd_info["max_command"]
                measure_ranges[full_name] = ranges
            self.__measure_range_cache = measure_ranges
        measure_func = self.MeasureFunctions[self.meas_func]["max_command"]
        return self.__measure_range_cache.get(measure_func, [])

    @BeaconObject.property(priority=2)
    def range(self):
        cmd = self._meas_func_sensor_cmd("RANGe:UPPer")
        return self.comm[cmd]

    @range.setter
    def range(self, range_value):
        """
        Adapt Keithley measurement range.
        usage: sensor.range = <value_to_measure>
        The best suited range will then be selected.
        """
        cmd = self._meas_func_sensor_cmd("RANGe:UPPer")
        value = range_value
        for value in self.possible_ranges:
            if value >= range_value:
                break

        self.auto_range = False
        print(f"{self.name} range set to {value}")
        self.comm[cmd] = value
        return self.comm[cmd]

    def _initialize_with_setting(self):
        self.__controller._initialize_with_setting()
        super()._initialize_with_setting()

    def _meas_func_sensor_cmd(self, param):
        func = self.meas_func
        return f"SENS{self.address}:{func}:{param}"

    def _sensor_cmd(self, param):
        return f"SENS{self.address}:{param}"

    def __info__(self):
        sinfo = f"Keithley {self.model}\n"
        sinfo += f"meas_func  = {self.meas_func}\n"
        sinfo += f"auto_range = {self.auto_range}\n"
        sinfo += f"range      = {self.range}\n"
        sinfo += f"nplc       = {self.nplc}\n"
        return sinfo


class SensorZeroCheckMixin:
    """
    Mixin to add Zero Check and Zero Correct
    """

    @BeaconObject.property(default=False)
    def zero_check(self):
        return self.comm["SYST:ZCH"]

    @zero_check.setter
    def zero_check(self, value):
        self.comm["SYST:ZCH"] = value

    @BeaconObject.property(default=False)
    def zero_correct(self):
        return self.comm["SYST:ZCOR"]

    @zero_correct.setter
    def zero_correct(self, value):
        self.comm["SYST:ZCOR"] = value

    def acquire_zero_correct(self):
        """Zero correct procedure"""
        zero_check = self.settings["zero_check"]
        zero_correct = self.settings["zero_correct"]
        self.zero_check = True  # zero check must be enabled
        self.zero_correct = False  # zero correct state must be disabled
        self.comm("INIT")  # trigger a reading
        self.comm("SYST:ZCOR:ACQ")  # acquire zero correct value
        self.zero_correct = zero_correct  # restore zero correct state
        self.zero_check = zero_check  # restore zero check

    def __info__(self):
        sinfo = f"zero_check = {self.zero_check}\n"
        sinfo += f"zero_correct = {self.zero_correct}"
        return sinfo


class BaseMultimeter(BeaconObject):
    class _CounterController(SamplingCounterController):
        def __init__(self, *args, comm=None, **kwargs):
            super().__init__(*args, **kwargs)
            self.__comm = comm

        def read_all(self, *counters):
            for counter in counters:
                counter._initialize_with_setting()
            values = self.__comm["READ"]
            return [values[cnt.index] for cnt in counters]

    def __init__(self, config, interface=None):
        self.__name = config.get("name", "keithley")
        kwargs = dict(config)
        if interface:
            kwargs["interface"] = interface
        BeaconObject.__init__(self, config)
        self._keithley_comm = KeithleySCPI(**kwargs)
        comm = self._keithley_comm

        self._counter_controller = BaseMultimeter._CounterController(
            "keithley", comm=comm
        )
        max_freq = config.get("max_sampling_frequency")
        self._counter_controller.max_sampling_frequency = max_freq
        global_map.register(self)

    def __str__(self):
        return f"{self.__class__.__name__}({self.name})"

    @property
    def name(self):
        return self.__name

    def _initialize_with_setting(self):
        is_initialized = self._is_initialized
        if not is_initialized:
            self._keithley_comm("*RST", "*OPC?")

        super()._initialize_with_setting()

        if not is_initialized:
            self._keithley_comm("*OPC?")

    @BeaconObject.property(default=True)
    def display_enable(self):
        return self._keithley_comm["DISP:ENAB"]

    @display_enable.setter
    def display_enable(self, value):
        self._keithley_comm["DISP:ENAB"] = value

    @BeaconObject.property(default=False)
    def auto_zero(self):
        return self._keithley_comm["SYST:AZER"]

    @auto_zero.setter
    def auto_zero(self, value):
        self._keithley_comm["SYST:AZER"] = value

    @BeaconObject.lazy_init
    def abort(self):
        return self._keithley_comm("ABOR", "OPC?")

    @BeaconObject.lazy_init
    def __info__(self):
        values = self.settings.get_all()
        settings = "\n".join((f"    {k}={v}" for k, v in values.items()))
        idn = "\n".join(
            (f"    {k}={v}" for k, v in self._keithley_comm["*IDN"].items())
        )
        return f"{self}:\n  IDN:\n{idn}\n  settings:\n{settings}\n"

    class Sensor(BaseSensor):
        pass


class K6485(BaseMultimeter, HasMetadataForScanExclusive):
    def _initialize_with_setting(self):
        if not self._is_initialized:
            self._keithley_comm["FORM:ELEM"] = [
                "READ"
            ]  # just get the current when you read (no timestamp)
            self._keithley_comm["CALC3:FORM"] = "MEAN"  # buffer statistics is mean
            self._keithley_comm["TRAC:FEED"] = "SENS"  # source of reading is sensor
        super()._initialize_with_setting()

    class Sensor(BaseMultimeter.Sensor, SensorZeroCheckMixin):
        @property
        def meas_func(self):
            """
            Fixed the measure function to Current
            """
            return "CURR"

        def __info__(self):
            return BaseMultimeter.Sensor.__info__(self) + SensorZeroCheckMixin.__info__(
                self
            )

        def scan_metadata(self):
            metadata = dict()
            metadata["@NX_class"] = "NXcollection"
            metadata["measure"] = self.meas_func
            metadata["auto_range"] = self.auto_range
            metadata["range"] = self.range
            return metadata


class K6487(K6485):
    class Sensor(K6485.Sensor):
        @BeaconObject.property
        def source_range(self):
            return self.comm["SOURce1:VOLTage:RANGe"]

        @source_range.setter
        def source_range(self, value):
            self.comm["SOURce1:VOLTage:RANGe"] = value
            # read one value to enable display
            self.comm["READ"]

        @BeaconObject.property
        def source_enable(self):
            return self.comm["SOURce1:VOLTage:STATe"]

        @source_enable.setter
        def source_enable(self, value):
            self.comm["SOURce1:VOLTage:STATe"] = value

        @BeaconObject.property
        def source_value(self):
            return self.comm["SOURce1:VOLTage:LEVel:IMMediate:AMPLitude"]

        @source_value.setter
        def source_value(self, value):
            self.comm["SOURce1:VOLTage:LEVel:IMMediate:AMPLitude"] = value


class K6482(BaseMultimeter):
    def _initialize_with_setting(self):
        if not self._is_initialized:
            self._keithley_comm["FORM:ELEM"] = ["CURR1", "CURR2"]
            self._keithley_comm["CALC8:FORM"] = "MEAN"  # buffer statistics is mean
        super()._initialize_with_setting()

    class Sensor(BaseMultimeter.Sensor):
        @property
        def meas_func(self):
            """
            Fixed the measure function to Current
            """
            return "CURR"


class K6514(BaseMultimeter, HasMetadataForScanExclusive):
    def _initialize_with_setting(self):
        if not self._is_initialized:
            self._keithley_comm["FORM:ELEM"] = [
                "READ"
            ]  # just get the current when you read (no timestamp)
            self._keithley_comm["CALC3:FORM"] = "MEAN"  # buffer statistics is mean
            self._keithley_comm["TRAC:FEED"] = "SENS"  # source of reading is sensor
        super()._initialize_with_setting()

    class Sensor(BaseSensor, SensorZeroCheckMixin):
        MeasureFunctions = SCPICommands(
            {
                "VOLTage[:DC]": SCPICmd(),
                "CURRent[:DC]": SCPICmd(),
                "RESistance": SCPICmd(),
                "CHARge": SCPICmd(),
            }
        )
        MeasureRanges = {
            "CURRENT:DC": [
                20e-12,
                200e-12,
                2e-9,
                20e-9,
                200e-9,
                2e-6,
                20e-6,
                200e-6,
                2e-3,
                20e-3,
            ],
            "VOLTAGE:DC": [2, 20, 200],
            "RESISTANCE": [2e3, 20e3, 200e3, 2e6, 20e6, 200e6, 2e9, 20e9, 200e9],
            "CHARGE": [20e-9, 200e-9, 2, 20],
        }

        def __info__(self):
            return BaseSensor.__info__(self) + SensorZeroCheckMixin.__info__(self)

        def scan_metadata(self):
            metadata = dict()
            metadata["@NX_class"] = "NXcollection"
            metadata["measure"] = self.meas_func
            metadata["auto_range"] = self.auto_range
            metadata["range"] = self.range
            return metadata


class K2000(BaseMultimeter):
    @staticmethod
    def Sensor(config, ctrl):
        meas_func = config.get("meas_func", "")
        if meas_func.startswith("TEMP"):
            return K2000.ThermocoupleSensor(config, ctrl)
        return K2000.MultimeterSensor(config, ctrl)

    class MultimeterSensor(BaseMultimeter.Sensor):
        MeasureFunctions = SCPICommands(
            {
                "CURRent[:DC]": SCPICmd(),
                "CURRent:AC": SCPICmd(),
                "VOLTage[:DC]": SCPICmd(),
                "VOLTage:AC": SCPICmd(),
                "RESistance": SCPICmd(),
                "FRESistance": SCPICmd(),
                "PERiod": SCPICmd(),
                "FREQuency": SCPICmd(),
            }
        )

    class _BaseTempSensor(SamplingCounter, BeaconObject):
        name = BeaconObject.config_getter("name")
        address = BeaconObject.config_getter("address")

        def __init__(self, config, controller):
            BeaconObject.__init__(self, config)
            SamplingCounter.__init__(self, self.name, controller._counter_controller)
            self.__controller = controller

        @autocomplete_property
        def comm(self):
            return self.__controller._keithley_comm

        @autocomplete_property
        def controller(self):
            return self.__controller

        @property
        def index(self):
            return self.address - 1

        @property
        def meas_func(self):
            return "TEMPerature"

        @BeaconObject.property
        def nplc(self):
            return self.comm["TEMP:NPLC"]

        @nplc.setter
        def nplc(self, value):
            self.comm["TEMP:NPLC"] = value

        @BeaconObject.property(doc="Specify measurement resolution (4 to 7)")
        def measurement_resolution(self):
            return self.comm["TEMP:DIG"]

        @measurement_resolution.setter
        def measurement_resolution(self, value):
            self.comm["TEMP:DIG"] = value

        def _initialize_with_setting(self):
            if not self._is_initialized:
                self.comm("CONF:TEMP")
            super()._initialize_with_setting()

    class ThermocoupleSensor(_BaseTempSensor):
        @BeaconObject.property(doc="Select thermocouple type (J, K, or T)")
        def thermocouple_type(self):
            return self.comm["TEMPerature:TC:TYPE"]

        @thermocouple_type.setter
        def thermocouple_type(self, value):
            self.comm["TEMPerature:TC:TYPE"] = value

        def __info__(self):
            info = "meas_func = TEMPerature\n"
            info += f"thermocouple_type = {self.thermocouple_type}\n"
            info += f"measurement_resolution = {self.measurement_resolution} Digits\n"
            info += f"nplc = {self.nplc}\n"
            return info


class K2700(K2000):
    @staticmethod
    def Sensor(config, ctrl):
        meas_func = config.get("meas_func", "")
        if meas_func.startswith("TEMP"):
            if config.get("fourwrtd_type"):
                return K2700.FourwrtdTempSensor(config, ctrl)
            if config.get("thermistor_type"):
                return K2700.ThermistorSensor(config, ctrl)
            return K2700.TermocoupleSensor(config, ctrl)
        return K2700.MultimeterSensor(config, ctrl)

    class _BaseTempSensor(K2000._BaseTempSensor):
        def __info__(self):
            info = "meas_func = TEMP\n"
            session_config = current_session.config
            object_config = session_config.get_config(self.name)
            for key, val in object_config.items():
                if "_type" in key:
                    info += f"{key} = {val}\n"
            info += f"measurement_resolution = {self.measurement_resolution} Digits\n"
            info += f"nplc = {self.nplc}\n"
            return info

    class TermocoupleSensor(_BaseTempSensor):
        @BeaconObject.property(
            doc="Select thermocouple type (J, K, N, T, E, R, S or B)"
        )
        def thermocouple_type(self):
            return self.comm["TEMPerature:TC:TYPE"]

        @thermocouple_type.setter
        def thermocouple_type(self, value):
            self.comm["TEMPerature:TRANSducer"] = "TC"
            self.comm["TEMPerature:TC:TYPE"] = value

    class FourwrtdTempSensor(_BaseTempSensor):
        @BeaconObject.property(
            doc="Select resistance temperature detector type (PT100, D100, F100, PT3916 or PT385)"
        )
        def fourwrtd_type(self):
            return self.comm["TEMPerature:FRTD:TYPE"]

        @fourwrtd_type.setter
        def fourwrtd_type(self, value):
            self.comm["TEMPerature:TRANSducer"] = "FRTD"
            self.comm["TEMPerature:FRTD:TYPE"] = value

    class ThermistorSensor(_BaseTempSensor):
        @BeaconObject.property(doc="Select thermistor type in ohms (1950 to 10050)")
        def thermistor_type(self):
            return self.comm["TEMPerature:THERmistor:TYPE"]

        @thermistor_type.setter
        def thermistor_type(self, value):
            self.comm["TEMPerature:TRANSducer"] = "THER"
            self.comm["TEMPerature:THERmistor:TYPE"] = value


class K2410(BaseMultimeter):
    @staticmethod
    def Sensor(config, ctrl):
        return K2410.MultimeterSensor(config, ctrl)

    class MultimeterSensor(BaseMultimeter.Sensor):
        MeasureFunctions = SCPICommands(
            {
                "CURRent[:DC]": SCPICmd(),
                "CURRent:AC": SCPICmd(),
                "VOLTage[:DC]": SCPICmd(),
                "VOLTage:AC": SCPICmd(),
                "RESistance": SCPICmd(),
                "FRESistance": SCPICmd(),
                "PERiod": SCPICmd(),
                "FREQuency": SCPICmd(),
            }
        )

        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            self._disabled_settings = HashObjSetting(f"{self.name}:disabled_settings")
            self._disabled_settings["source_max_value"] = True

        def apply_config(self, *args, **kw):
            self._disabled_settings["source_max_value"] = False
            try:
                super().apply_config(*args, **kw)
            finally:
                self._disabled_settings["source_max_value"] = True

        def __info__(self, show_module_info=True):
            sinfo = super().__info__()
            sinfo += f"Source function = {self.source_func}\n"
            sinfo += f"Source range = {self.source_range}\n"
            sinfo += f"Source compliant value = {self.source_cmpl}\n"
            sinfo += f"Source value = {self.source_value}\n"
            sinfo += f"Source max value = {self.source_max_value}\n"
            if self.source_enable == 1:
                output_state = "ON"
            else:
                output_state = "OFF"

            sinfo += f"Source enable = {output_state}\n"
            return sinfo

        @BeaconObject.property(default="VOLT:DC", priority=-1)
        def meas_func(self):
            return super()._meas_func()

        @meas_func.setter
        def meas_func(self, func):
            func = self.MeasureFunctions[func]["max_command"]
            self.comm("CONF:" + func)
            # remove range and auto_range in settings
            if not self._in_initialize_with_setting:
                with pipeline(self.settings):
                    del self.settings["auto_range"]
                    del self.settings["range"]

            self.comm["FORM:ELEM"] = [func[0:4]]
            return func

        @BeaconObject.property(priority=-1)
        def range(self):
            if self.meas_func.find("CURR") != -1:
                return self.comm["SENSe1:CURRent:DC:RANGe"]
            else:
                return self.comm["SENSe1:VOLTage:DC:RANGe"]

        @range.setter
        def range(self, value):
            if self.meas_func.find("CURR") != -1:
                self.comm["SENSe1:CURRent:DC:RANGe"] = value
            else:
                self.comm["SENSe1:VOLTage:DC:RANGe"] = value

        @BeaconObject.property(default="VOLT")
        def source_func(self):
            self.source_func = self.comm["SOURce1:FUNCtion"]
            return self.source_func

        @source_func.setter
        def source_func(self, value):
            self.comm["SOURce1:FUNCtion"] = value
            if value == "VOLT":
                self.comm["SOURce1:VOLTage:MODE"] = "FIX"
            else:
                self.comm["SOURce1:CURRent:MODE"] = "FIX"

        @BeaconObject.property
        def source_range(self):
            if self.source_func == "VOLT":
                return self.comm["SOURce1:VOLTage:RANGe"]
            else:
                return self.comm["SOURce1:CURRent:RANGe"]

        @source_range.setter
        def source_range(self, value):
            if self.source_func == "VOLT":
                self.comm["SOURce1:VOLTage:RANGe"] = value
            else:
                self.comm["SOURce1:CURRent:RANGe"] = value

        @BeaconObject.property
        def source_cmpl(self):
            if self.source_func == "VOLT":
                return self.comm["SENSe1:CURRent:PROT:LEVel"]
            else:
                return self.comm["SENSe1:VOLTage:PROT:LEVel"]

        @source_cmpl.setter
        def source_cmpl(self, value):
            if self.source_func == "VOLT":
                self.comm["SENSe1:CURRent:PROT:LEVel"] = value
            else:
                self.comm["SENSe1:Voltage:PROT:LEVel"] = value

        @BeaconObject.property
        def source_value(self):
            if self.source_func == "VOLT":
                return self.comm["SOURce1:VOLTage:LEVel:IMMediate:AMPLitude"]
            else:
                return self.comm["SOURce1:CURRent:LEVel:IMMediate:AMPLitude"]

        @source_value.setter
        def source_value(self, value):
            if self.source_func == "VOLT":
                self.comm["SOURce1:VOLTage:LEVel:IMMediate:AMPLitude"] = value
            else:
                self.comm["SOURce1:CURRent:LEVel:IMMediate:AMPLitude"] = value

        @BeaconObject.property(priority=-1)
        def source_max_value(self):
            if self.source_func == "VOLT":
                return self.comm["SOURce1:VOLTage:PROT:LEVel"]
            else:
                return self.comm["SOURce1:CURRent:PROT:LEVel"]

        @source_max_value.setter
        def source_max_value(self, value):
            if self.source_func == "VOLT":
                self.comm["SOURce1:VOLTage:PROT:LEVel"] = value
                value = self.comm["SOURce1:VOLTage:PROT:LEVel"]
            else:
                self.comm["SOURce1:CURRent:PROT:LEVel"] = value
                value = self.comm["SOURce1:CURRent:PROT:LEVel"]

        @BeaconObject.property(priority=-2)
        def source_enable(self):
            return self.comm["OUTP:STAT"]

        @source_enable.setter
        def source_enable(self, value):
            self.comm["OUTP:STAT"] = value


class K2450(BaseMultimeter, HasMetadataForScanExclusive):
    def _initialize_with_setting(self):
        if not self._is_initialized:
            self.clear_logs()
        super()._initialize_with_setting()

    def clear_logs(self):
        self._keithley_comm("SYST:CLE")

    @BeaconObject.property(default=True)
    def display_enable(self):
        return True

    @display_enable.setter
    def display_enable(self, value):
        pass

    class Sensor(BaseMultimeter.Sensor):
        MeasureFunctions = SCPICommands(
            {
                "CURRent[:DC]": SCPICmd(),
                "VOLTage[:DC]": SCPICmd(),
                "RESistance": SCPICmd(),
            }
        )
        MeasureRanges = {
            "CURRENT:DC": [10e-9, 100e-9, 1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3, 1],
            "VOLTAGE:DC": [20e-3, 200e-3, 2, 20, 200],
            "RESISTANCE": [2, 20, 200, 2e3, 20e3, 200e3, 2e6, 20e6, 200e6],
        }

        def __init__(self, config, controller):
            super().__init__(config, controller)
            self.__source_axis = None

        def __info__(self, show_module_info=True):
            sinfo = super().__info__()
            sinfo += "\n"
            sinfo += f"source_func        = {self.source_func}\n"
            onoff = self.source_enable and "ON" or "OFF"
            sinfo += f"source_enable      = {onoff}\n"
            sinfo += f"source_value       = {self.source_value}\n"
            onoff = self.source_auto_range and "ON" or "OFF"
            sinfo += f"source_auto_range  = {onoff}\n"
            sinfo += f"source_range       = {self.source_range}\n"
            if self.source_func == "VOLT":
                limtxt = "current"
            else:
                limtxt = "voltage"
            sinfo += f"source_limit       = {self.source_limit} [{limtxt}]\n"

            return sinfo

        def _set(self, key, value):
            self.comm[key] = value
            # --- check_errors
            errs = self.comm.language.get_errors()
            if errs is not None:
                for err in errs:
                    (code, desc) = err.values()
                    txt = f"Keithly error #{code}, {desc}"
                log_warning(self, txt)
            # --- read back value
            return self.comm[key]

        @BeaconObject.property(default="CURR:DC", priority=-1)
        def meas_func(self):
            return super()._meas_func()

        @meas_func.setter
        def meas_func(self, func):
            try:
                func = self.MeasureFunctions.get_min_command(func)
            except KeyError:
                raise ValueError("Invalid measure function")
            func = self._set("SENS1:FUNC", f'"{func}"')
            func = func.replace('"', "")
            # remove range and auto_range in settings
            if not self._in_initialize_with_setting:
                with pipeline(self.settings):
                    del self.settings["auto_range"]
                    del self.settings["range"]
            return func

        @BeaconObject.property(priority=2)
        def range(self):
            cmd = self._meas_func_sensor_cmd("RANGe:UPPer")
            return self.comm[cmd]

        @range.setter
        def range(self, range_value):
            cmd = self._meas_func_sensor_cmd("RANGe:UPPer")
            return self._set(cmd, range_value)

        @BeaconObject.property(default="VOLT")
        def source_func(self):
            self.source_func = self.comm["SOUR1:FUNC"]
            return self.source_func

        @source_func.setter
        def source_func(self, value):
            setfunc = self._set("SOUR1:FUNC", value)
            if self.__source_axis is not None:
                self.__source_axis._unit = self._axis_unit(setfunc)
            return setfunc

        @BeaconObject.property(priority=-2)
        def source_enable(self):
            return self.comm["OUTP:STAT"]

        @source_enable.setter
        def source_enable(self, value):
            return self._set("OUTP:STAT", value)

        @BeaconObject.property
        def source_value(self):
            source = self.source_func
            return self.comm[f"SOUR1:{source}:LEV:IMM:AMPL"]

        @source_value.setter
        def source_value(self, value):
            source = self.source_func
            return self._set(f"SOUR1:{source}:LEV:IMM:AMPL", value)

        @property
        def source_limit(self):
            if self.source_func == "VOLT":
                return self.comm["SOUR1:VOLT:ILIM"]
            else:
                return self.comm["SOUR1:CURR:VLIM"]

        @source_limit.setter
        def source_limit(self, value):
            if self.source_func == "VOLT":
                return self._set("SOUR1:VOLT:ILIM", value)
            else:
                return self._set("SOUR1:CURR:VLIM", value)

        @BeaconObject.property
        def source_range(self):
            source = self.source_func
            return self.comm[f"SOUR1:{source}:RANG"]

        @source_range.setter
        def source_range(self, value):
            source = self.source_func
            return self._set(f"SOUR1:{source}:RANG", value)

        @property
        def source_possible_ranges(self):
            source = self.source_func
            sname = self.MeasureFunctions.get_max_command(source)
            return self.MeasureRanges[sname]

        @BeaconObject.property(priority=1)
        def source_auto_range(self):
            source = self.source_func
            return self.comm[f"SOUR1:{source}:RANG:AUTO"]

        @source_auto_range.setter
        def source_auto_range(self, value):
            source = self.source_func
            value = self._set(f"SOUR1:{source}:RANG:AUTO", value)
            if value:
                self.disable_setting("source_range")
            else:
                self.enable_setting("source_range")
            return value

        def scan_metadata(self):
            metadata = dict()
            metadata["@NX_class"] = "NXcollection"
            metadata["measure"] = self.meas_func
            metadata["auto_range"] = self.auto_range
            metadata["range"] = self.range
            if self.source_enable:
                metadata["source_func"] = self.source_func
                metadata["source_value"] = self.source_value
            return metadata

        @property
        def source_axis(self):
            if self.__source_axis is None:
                unit = self._axis_unit()
                self.__source_axis = SoftAxis(
                    f"{self.name}_axis",
                    self,
                    move="source_value",
                    position="source_value",
                    state="_axis_state",
                    unit=unit,
                )
            return self.__source_axis

        @BeaconObject.property
        def export_axis(self):
            if self.__source_axis is None:
                return False
            else:
                return self.__source_axis._positioner

        @export_axis.setter
        def export_axis(self, value):
            if value:
                axis = self.source_axis
                axis._positioner = True
            else:
                if self.__source_axis is not None:
                    self.__source_axis._positioner = False

        def _axis_state(self):
            if not self.source_enable:
                return AxisState("DISABLED")
            else:
                return AxisState("READY")

        def _axis_unit(self, func=None):
            if func is None:
                func = self.source_func
            if func.startswith("VOLT"):
                return "V"
            elif func.startswith("CURR"):
                return "A"
            else:
                return None


class K2460(K2450):
    pass


class K2470(K2450):
    class Sensor(K2450.Sensor):
        MeasureRanges = {
            "CURRENT:DC": [10e-9, 100e-9, 1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3, 1],
            "VOLTAGE:DC": [200e-3, 2, 20, 200, 1000],
            "RESISTANCE": [2, 20, 200, 2e3, 20e3, 200e3, 2e6, 20e6, 200e6],
        }


class AmmeterDDCCounterController(SamplingCounterController):
    def __init__(self, name, interface):
        super().__init__(name)
        self.interface = interface

    def read_all(self, *counters):
        for counter in counters:
            counter._initialize_with_setting()
        values = self.interface.write_readline(b"X\r\n")
        return [values]


class AmmeterDDC(BeaconObject):
    def __init__(self, config):
        self.__name = config.get("name", "keithley")
        interface = get_comm(config, eol="\r\n")
        super().__init__(config)

        self._counter_controller = AmmeterDDCCounterController("keithley", interface)
        self._counter_controller.max_sampling_frequency = config.get(
            "max_sampling_frequency", 1
        )

    @property
    def name(self):
        return self.__name

    def __str__(self):
        return f"{self.__class__.__name__}({self.name})"

    class Sensor(SamplingCounter, BeaconObject):
        name = BeaconObject.config_getter("name")
        address = BeaconObject.config_getter("address")

        def __init__(self, config, controller):
            BeaconObject.__init__(self, config)
            SamplingCounter.__init__(self, self.name, controller._counter_controller)
            self.__controller = controller
            self.__interface = controller._counter_controller.interface
            self.__model = self.__controller.config["model"]

            if self.__model == 6512:
                self._ranges = [
                    2e-12,
                    2e-11,
                    2e-10,
                    2e-9,
                    2e-8,
                    2e-7,
                    2e-6,
                    2e-5,
                    2e-4,
                    2e-3,
                    2e-2,
                ]
            else:
                self._ranges = [2e-9, 2e-8, 2e-7, 2e-6, 2e-5, 0.0002, 0.002, 0.02]

        def __info__(self):
            sinfo = f"Keithley {self.__model}\n"
            sinfo += f"auto_range = {self.auto_range}\n"
            sinfo += f"range = {self.range}\n"
            return sinfo

        @property
        def model(self):
            return self.__model

        @property
        def comm(self):
            return self.__interface

        @property
        def index(self):
            return 0

        def _initialize_with_setting(self):
            if not self._is_initialized:
                self.__interface.write(b"F1X\r\n")  # Amp function
                self.__interface.write(b"B0X\r\n")  # electrometer reading
                self.__interface.write(b"G1X\r\n")  # Reading without prefix
                self.__interface.write(b"T4X\r\n")
            super()._initialize_with_setting()

        @BeaconObject.property(priority=1)
        def auto_range(self):
            # query machine status word
            status = self.__interface.write_readline(b"U0X\r\n").decode()
            if self.model == 6512:
                # with this model status word is as "6512100000600107000=:"
                # model(4 digit), function(1 digit), range(2 digit)
                return int(status[5:7]) == 0
            r = status.partition("R")[2][:2]
            # to set autorange command is R0/R10, but return status
            # for R is 11 value if autorange otherwise the range value(1-9)
            return r[0] == "1"

        @auto_range.setter
        def auto_range(self, value):
            if value:
                cmd = b"R0\r\n"
                self.disable_setting("range")
            else:
                if self.model == 6512:
                    cmd = b"R10\r\n"
                else:
                    cmd = b"R12\r\n"
                self.enable_setting("range")
            self.__interface.write(cmd)

        @property
        def possible_ranges(self):
            """
            Return the possible ranges for the current
            measure functions.
            """

            return self._ranges

        @BeaconObject.property(priority=2)
        def range(self):
            # query machine status word
            status = self.__interface.write_readline(b"U0X\r\n").decode()
            if self.model == 6512:
                # with this model status word is as "6512100000600107000=:"
                # model(4 digit), function(1 digit), range(2 digit)
                r = int(status[5:7])
                if r == 0:
                    return "AUTO"
                return self._ranges[r - 1]
            r = status.partition("R")[2][:2]
            # first digit mean auto (1) or manual (0)
            # second digit is the range
            if r[0] == "1":
                r = r[1]
            return self._ranges[int(r) - 1]

        @range.setter
        def range(self, range_value):
            try:
                range = self._ranges.index(range_value) + 1
            except ValueError:
                print(f"{range_value} not a supported range")
                return
            cmd = f"R{range}X\r\n"
            self.__interface.write(cmd.encode())
            self.auto_range = False


class K6512(AmmeterDDC):
    pass


class K485(AmmeterDDC):
    pass


class K486(AmmeterDDC):
    pass


class K487(AmmeterDDC):
    pass


def Multimeter(config):
    model = config.get("model")
    kwargs = {}
    if model is None:
        # Discover model
        interface, _, _ = get_interface(**config)
        decode_IDN = SCPI_COMMANDS["*IDN"].get("get")
        idn = decode_IDN(interface.write_readline(b"*IDN?\n").decode())
        model = idn["model"]
        kwargs["interface"] = interface
        config["model"] = model
    else:
        model = str(model)
    class_name = f"K{model}"
    try:
        klass = globals()[class_name]
    except KeyError as err:
        raise ValueError(
            f"Unknown keithley model {model} (hint: DDC needs a model " "in YAML)"
        ) from err
    obj = klass(config, **kwargs)
    return obj


class TangoKeithley(CounterContainer):
    """Class to connect to Tango Keithley device server."""

    def __init__(self, config):
        super().__init__()
        tango_url = config.get("tango").get("url")
        self._tango_proxy = DeviceProxy(tango_url)
        name = config["name"]

        # --- diode counter controller
        self._counter_controller = TangoKeithleyCounterController(
            name, self._tango_proxy, register_counters=False
        )
        self._diode_counter = self._counter_controller.create_counter(
            SamplingCounter, name, unit="uA", mode="SINGLE"
        )

        self.name = name

        global_map.register(self, parents_list=["counters"])

    @autocomplete_property
    def counters(self):
        """Return the available for a scan counters.

        Returns:
            (counters): Countes object.
        """
        return self._counter_controller.counters

    @property
    def diode(self):
        """Return the diode object as property.

        Returns:
            (counter): The diode counter object
        """
        return self._diode_counter

    @property
    def auto_range(self):
        """Read the autorange mode.

        Returns:
            (bool): True if in autorange mode, False otherwise.
        """
        return self._tango_proxy.auto_range

    @auto_range.setter
    def auto_range(self, value):
        """Set the autorange mode.

        Args:
            value(bool): True if autorange mode, False otherwise.
        """
        self._tango_proxy.auto_range = bool(value)

    @property
    def auto_zero(self):
        """Read the autozero mode.

        Returns:
            (bool): True if in autozero mode, False otherwise.
        """
        return self._tango_proxy.auto_zero

    @auto_zero.setter
    def auto_zero(self, value):
        """Set the autozero mode.

        Args:
            value (bool): True if autozero mode, False otherwise.
        """
        self._tango_proxy.auto_zero = bool(value)

    @property
    def zero_correct(self):
        """Read the zero correction mode.

        Returns:
            (bool): True if zero correction set, False otherwise.
        """
        return self._tango_proxy.zero_correct

    @zero_correct.setter
    def zero_correct(self, value):
        """Set the zero correction mode.

        Args:
            value (bool): True if zero correction, False otherwise.
        """
        self._tango_proxy.zero_correct = bool(value)

    @property
    def zero_check(self):
        """Read the zero check mode.

        Returns:
            (bool): True if zero check set, False otherwise.
        """
        return self._tango_proxy.zero_check

    @zero_check.setter
    def zero_check(self, value):
        """Set the zero check mode.

        Args:
            value (bool): True if zero correction, False otherwise.
        """
        self._tango_proxy.zero_check = bool(value)

    @property
    def range(self):
        """Read the current range.

        Retuns:
            (float): Current range [V].
        """
        return self._tango_proxy.range

    @range.setter
    def range(self, value):
        """Set the current range.

        Warning: this cancels the auto range.

        Args:
            value(float): Range [V]
        """
        self._tango_proxy.range = value

    @property
    def possible_ranges(self):
        """Get the possible range values.

        Returns:
            (list): the available ranges.
        """
        return str(self._tango_proxy.possible_ranges)

    @property
    def rate(self):
        """Read the acquisition rate mode.

        Returns:
            (float): The value.
        """
        return self._tango_proxy.rate

    @rate.setter
    def rate(self, value):
        """Set the acquisition rate mode.

        Args:
            value (float): The value.
        """
        self._tango_proxy.rate = float(value)

    def abort(self):
        """Abort execution"""
        self._tango_proxy.abort()

    def acquire_zero_correct(self):
        """Procedure to acquire the zero correct value."""
        self._tango_proxy.acquire_zero_correct()

    @property
    def raw_read(self):
        """Single read from the keithley.

        Retuns:
            (float): The value.
        """
        return self._tango_proxy.raw_read


class TangoKeithleyCounterController(SamplingCounterController):
    """Counter controller class"""

    def __init__(self, device_name, tg_proxy, register_counters=False):
        super().__init__(device_name, register_counters=register_counters)
        self._tango_proxy = tg_proxy

    def read(self, counter=None):
        """Read the counter
        Args:
            counter (obj): Counter object (not used here).
        Returns:
            (float): The value
        """
        # use the proxy and not the counter object as there is only one counter
        return self._tango_proxy.raw_read
