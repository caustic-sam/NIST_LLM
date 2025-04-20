# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
This module implements the classes allowing the control of regulation processes and associated hardware

    The regulation is a process that:

    1) reads a value from an input device
    2) takes a target value (`setpoint`) and compare it to the current input value (processed value)
    3) computes an output value sent to an output device which has an effect on the processed value
    4) back to step 1) and loop forever so that the processed value reaches the target value and stays stable around that target value.

    The regulation Loop has:

    -One input: an Input object to read the processed value (ex: temperature sensor)
    -One output: an Output object which has an effect on the processed value (ex: cooling device)

    The regulation is automatically started by setting a new `setpoint` (`Loop.setpoint = target_value`).
    The Loop object implements methods to manage the PID algorithm that performs the regulation.
    A Loop object is associated to one Input and one Output.

    The Loop object has a ramp object. If loop.ramprate != 0 then any new `setpoint` cmd (using `Loop.setpoint`)
    will use a ramp to reach that value (HW ramp if available else a `SoftRamp`).

    The Output object has a ramp object. If loop.output.ramprate != 0 then any new value sent to the output
    will use a ramp to reach that value (HW ramp if available else a `SoftRamp`).

    Depending on the hardware capabilities we can distinguish two main cases.

    1) Hardware regulation:

        A physical controller exists and the input and output devices are connected to the controller.
        In that case, a regulation Controller object must be implemented by inheriting from the `Controller` base class (`bliss.controllers.regulator`).
        The inputs and outputs attached to that controller are defined through the YML configuration file.

    .. code-block::

            ---------------------------------------------- YML file example ------------------------------------------------------------------------

            -
                class: Mockup                  # <-- the controller class inheriting from 'bliss.controllers.regulator.Controller'
                module: temperature.mockup
                host: lid42
                inputs:
                    -
                        name: thermo_sample
                        channel: A
                        unit: degC

                    -
                        name: sensor
                        channel: B

                outputs:
                    -
                        name: heater
                        channel: A
                        unit: Volt
                        low_limit:  0.0          # <-- minimum device value [unit]
                        high_limit: 100.0        # <-- maximum device value [unit]
                        ramprate: 0.0            # <-- ramprate to reach the output value [unit/s]

                ctrl_loops:
                    -
                        name: sample_regulation
                        input: $thermo_sample
                        output: $heater
                        P: 0.5
                        I: 0.2
                        D: 0.0
                        low_limit: 0.0           # <-- low limit of the PID output value. Usually equal to 0 or -1.
                        high_limit: 1.0          # <-- high limit of the PID output value. Usually equal to 1.
                        frequency: 10.0
                        deadband: 0.05
                        deadband_time: 1.5
                        ramprate: 1.0            # <-- ramprate to reach the setpoint value [input_unit/s]
                        wait_mode: deadband

            ----------------------------------------------------------------------------------------------------------------------------------------

    2) Software regulation

        Input and Output devices are not always connected to a regulation controller.
        For example, it may be necessary to regulate a temperature by moving a cryostream on a stage (axis).

        Any `SamplingCounter` can be interfaced as an input (`ExternalInput`) and any 'Axis' as an input or output (`ExternalOutput`).
        Devices which are not standard Bliss objects can be interfaced by implementing a custom input or output class inheriting from the Input/Output classes.

        To perform the regulation with this kind of inputs/outputs not attached to an hardware regulation controller, users must define a SoftLoop.
        The `SoftLoop` object inherits from the Loop class and implements its own PID algorithm (using the `simple_pid` Python module).

    .. code-block::

            ---------------------------------------------- YML file example ------------------------------------------------------------------------
            -
                class: MyDevice     # <== any kind of object (usually declared in another YML file)
                package: bliss.controllers.regulation.temperature.mockup
                plugin: bliss
                name: my_device

            -
                class: MyCustomInput     # <-- a custom input defined by the user and inheriting from the ExternalInput class
                package: bliss.controllers.regulation.temperature.mockup  # <-- the module where the custom class is defined
                plugin: bliss
                name: custom_input
                device: $my_device       # <-- any kind of object reference (pointing to an object declared somewhere else in a YML config file)
                unit: degC


            -
                class: MyCustomOutput    # <-- a custom output defined by the user and inheriting from the ExternalOutput class
                package: bliss.controllers.regulation.temperature.mockup  # <-- the module where the custom class is defined
                plugin: bliss
                name: custom_output
                device: $my_device       # <-- any kind of object reference (pointing to an object declared somewhere else in a YML config file)
                unit: W
                low_limit: 0.0           # <-- minimum device value [unit]
                high_limit: 100.0        # <-- maximum device value [unit]
                ramprate: 0.0            # <-- ramprate to reach the output value [unit/s]


            -
                class: ExternalInput     # <-- declare an 'ExternalInput' object
                name: diode_input
                device: $diode           # <-- a SamplingCounter object reference (pointing to a counter declared somewhere else in a YML config file )
                unit: N/A


            -
                class: ExternalOutput    # <-- declare an 'ExternalOutput' object
                name: robz_output
                device: $robz            # <-- an axis object reference (pointing to an axis declared somewhere else in a YML config file )
                unit: mm
                low_limit: -1.0          # <-- minimum device value [unit]
                high_limit: 1.0          # <-- maximum device value [unit]
                ramprate: 0.0            # <-- ramprate to reach the output value [unit/s]
                mode: relative           # <-- PID output value interpreted as a relative quantity (for relative axis motion) 


            -
                class: SoftLoop          # <== declare a 'SoftLoop' object
                name: soft_regul
                input: $custom_input
                output: $custom_output
                P: 0.05
                I: 0.1
                D: 0.0
                low_limit: 0.0            # <-- low limit of the PID output value. Usually equal to 0 or -1.
                high_limit: 1.0           # <-- high limit of the PID output value. Usually equal to 1.
                frequency: 10.0
                deadband: 0.1
                deadband_time: 3.0
                ramprate: 1.0
                wait_mode: deadband

                ------------------------------------------------------------------------------------------------------------------------------------

        Note: a SoftLoop can use an Input or Output defined in a regulation controller section.
        For example the 'soft_regul' loop could define 'thermo_sample' as its input.

"""

import time
import gevent
import gevent.event
import enum

from bliss import global_map
from bliss.common.protocols import CounterContainer, counter_namespace
from bliss.common.logtools import log_debug, log_error
from bliss.common.utils import with_custom_members, autocomplete_property
from bliss.common.counter import SamplingCounter
from bliss.controllers.counter import SamplingCounterController

from bliss.common.soft_axis import SoftAxis
from bliss.common.axis.axis import Axis
from bliss.common.axis.state import AxisState

from simple_pid import PID
from bliss.common.plot import get_flint

import functools


def lazy_init(func):
    @functools.wraps(func)
    def func_wrapper(self, *args, **kwargs):
        self.controller.init_obj(self)
        return func(self, *args, **kwargs)

    return func_wrapper


def _get_external_device_name(device):
    try:
        return f"{device.name} {device}"
    except AttributeError:
        return device


class SCC(SamplingCounterController):
    def __init__(self, name, boss):
        super().__init__(name, register_counters=False)
        self.boss = boss

    def read_all(self, *counters):
        return [self.boss.read()]


@with_custom_members
class Input(CounterContainer):
    """Implements the access to an input device which is accessed via the
    regulation controller (like a sensor plugged on a channel of the controller)
    """

    def __init__(self, controller, config):
        """Constructor"""
        self._name = config["name"]
        self._controller = controller
        self._config = config
        self._channel = self._config.get("channel")

        # useful attribute for a temperature controller writer
        self._attr_dict = {}

        self._scc = SCC(self.name, self)
        if self.controller is None:
            msf = self.config.get("max_sampling_frequency", 1)
        else:
            msf = self.controller.config.get("max_sampling_frequency", 1)
        self._scc.max_sampling_frequency = msf
        self._scc.create_counter(
            SamplingCounter, self.name, unit=self.config.get("unit"), mode="SINGLE"
        )

        global_map.register(self, parents_list=["counters"])

    @property
    def name(self):
        return self._name

    @autocomplete_property
    def counters(self):
        return self._scc.counters

    # ----------- BASE METHODS -----------------------------------------

    def load_base_config(self):
        """Load from the config the values of the standard parameters"""

        # below the parameters that may requires communication with the controller

        pass

    @property
    def controller(self):
        """Return the associated regulation controller"""

        return self._controller

    @property
    def config(self):
        """Return the Input config"""

        return self._config

    @property
    def channel(self):
        return self._channel

    # ----------- METHODS THAT A CHILD CLASS MAY CUSTOMIZE ------------------

    @lazy_init
    def __info__(self):
        lines = ["\n"]
        lines.append(f"=== Input: {self.name} ===")
        lines.append(
            f"controller: {self.controller.name if self.controller.name is not None else self.controller.__class__.__name__}"
        )
        lines.append(f"channel: {self.channel}")
        lines.append(
            f"current value: {self.read():.3f} {self.config.get('unit', 'N/A')}"
        )
        return "\n".join(lines)

    @lazy_init
    def read(self):
        """Return the input device value (in input unit)"""

        value = self._controller.read_input(self)
        log_debug(self, f"{self.name} read = {value}")
        return value

    @lazy_init
    def state(self):
        """Return the input device state"""

        value = self._controller.state_input(self)
        log_debug(self, f"{self.name} state = {value}")
        return value

    def allow_regulation(self):
        """This method is called by the SoftLoop to check if the regulation
        should be suspended.

        If this method returns False, the SoftLoop will pause the PID algorithm
        that computes the output value. As soon as this method returns True, the
        PID algorithm is resumed.

        While returning False, you must ensure that the read method of the Input
        still returns a numerical value (like the last readable value).
        """
        return True


class ExternalInput(Input):
    """Implements the access to an external input device (i.e. not accessed via
    the regulation controller itself, like an axis or a counter)

    Managed devices are objects of the type:
    - `Axis`
    - `SamplingCounter`
    """

    def __init__(self, config):
        super().__init__(None, config)

        self.device = config.get("device")
        self.load_base_config()

        self._controller = self._scc

    def __info__(self):
        lines = ["\n"]
        lines.append(f"=== ExternalInput: {self.name} ===")

        lines.append(f"device: {_get_external_device_name(self.device)}")
        lines.append(
            f"current value: {self.read():.3f} {self.config.get('unit', 'N/A')}"
        )
        return "\n".join(lines)

    def read(self):
        """Return the input device value (in input unit)"""

        if isinstance(self.device, Axis):
            value = self.device.position
        elif isinstance(self.device, SamplingCounter):
            value = self.device._counter_controller.read_all(self.device)[0]
        else:
            raise TypeError(
                "the associated device must be an 'Axis' or a 'SamplingCounter'"
            )

        log_debug(self, f"{self.name} read = {value}")
        return value

    def state(self):
        """Return the input device state"""

        if isinstance(self.device, Axis):
            value = self.device.state
        elif isinstance(self.device, SamplingCounter):
            value = "READY"
        else:
            raise TypeError(
                "the associated device must be an 'Axis' or a 'SamplingCounter'"
            )

        log_debug(self, f"{self.name} state = {value}")
        return value


@with_custom_members
class Output(CounterContainer):
    """Implements the access to an output device which is accessed via the regulation controller (like an heater plugged on a channel of the controller)

    The Output has a ramp object.
    If ramprate != 0 then any new value sent to the output
    will use a ramp to reach that value (hardware ramping if available, else a software ramp).

    """

    def __init__(self, controller, config):
        """Constructor"""

        self._name = config["name"]

        self._controller = controller
        self._config = config
        self._channel = self._config.get("channel")

        self._ramp = SoftRamp(self.read, self._set_value)
        self._use_soft_ramp = None

        self._limits = (
            self._config.get("low_limit", None),
            self._config.get("high_limit", None),
        )

        # useful attribute for a temperature controller writer
        self._attr_dict = {}

        self._scc = SCC(self.name, self)
        if self.controller is None:
            msf = self.config.get("max_sampling_frequency", 1)
        else:
            msf = self.controller.config.get("max_sampling_frequency", 1)
        self._scc.max_sampling_frequency = msf
        self._scc.create_counter(
            SamplingCounter, self.name, unit=self.config.get("unit"), mode="SINGLE"
        )

        global_map.register(self, parents_list=["counters"])

    @property
    def name(self):
        return self._name

    @autocomplete_property
    def counters(self):
        return self._scc.counters

    # ----------- BASE METHODS -----------------------------------------

    def load_base_config(self):
        """Load from the config the value of the standard parameters"""

        # below the parameters that may requires communication with the controller

        if self._config.get("ramprate") is not None:
            self.ramprate = self._config.get("ramprate")

    @property
    def controller(self):
        """Return the associated regulation controller"""

        return self._controller

    @property
    def config(self):
        """Return the Output config"""

        return self._config

    @property
    def limits(self):
        """Return the limits of the output device (in output unit)"""

        return self._limits

    @property
    def channel(self):
        return self._channel

    @autocomplete_property
    def soft_ramp(self):
        """Get the software ramp object"""

        return self._ramp

    def set_value(self, value):
        """Set `value` as new target and start ramping to this target (no ramping if ramprate==0)."""

        log_debug(self, f"{self.name} set_value {value}")

        if self._limits[0] is not None:
            value = max(value, self._limits[0])

        if self._limits[1] is not None:
            value = min(value, self._limits[1])

        self._start_ramping(value)

        return value

    def _add_custom_method(self, method, name, types_info=(None, None)):
        """Necessary to add custom methods to this class"""

        setattr(self, name, method)
        self.__custom_methods_list.append((name, types_info))

    # ----------- METHODS THAT A CHILD CLASS MAY CUSTOMIZE ------------------

    @lazy_init
    def __info__(self):
        lines = ["\n"]
        lines.append(f"=== Output: {self.name} ===")
        lines.append(
            f"controller: {self.controller.name if self.controller.name is not None else self.controller.__class__.__name__}"
        )
        lines.append(f"channel: {self.channel}")
        lines.append(
            f"current value: {self.read():.3f} {self.config.get('unit', 'N/A')}"
        )
        lines.append(f"state: {self.state()}")
        lines.append("\n=== Output.set_value ramping options ===")
        lines.append(f"ramprate: {self.ramprate}")
        lines.append(f"ramping: {self.is_ramping()}")
        lines.append(f"limits: {self._limits}")
        return "\n".join(lines)

    @lazy_init
    def state(self):
        """Return the state of the output device"""

        value = self._controller.state_output(self)
        log_debug(self, f"{self.name} state = {value}")
        return value

    @lazy_init
    def read(self):
        """Return the current value of the output device (in output unit)"""

        value = self._controller.read_output(self)
        log_debug(self, f"{self.name} read = {value}")
        return value

    @property
    @lazy_init
    def ramprate(self):
        """Get ramprate (in output unit per second)"""

        try:
            value = self._controller.get_output_ramprate(self)
        except NotImplementedError:
            value = self._ramp.rate

        log_debug(self, f"{self.name} ramprate = {value}")
        return value

    @ramprate.setter
    @lazy_init
    def ramprate(self, value):
        """Set ramprate (in output unit per second)"""

        log_debug(self, f"{self.name} ramprate set to {value}")

        self._ramp.rate = value
        try:
            self._controller.set_output_ramprate(self, value)
        except NotImplementedError:
            pass

    @lazy_init
    def is_ramping(self):
        """
        Get the ramping status.
        """

        if (
            self._use_soft_ramp is None
        ):  # case where '_start_ramping' was never called previously.
            try:
                value = self._controller.is_output_ramping(self)
            except NotImplementedError:
                value = False
        elif self._use_soft_ramp:
            value = self._ramp.is_ramping()
        else:
            value = self._controller.is_output_ramping(self)

        log_debug(self, f"{self.name} is_ramping = {value}")

        return value

    @lazy_init
    def _set_value(self, value):
        """Set the value for the output. Value is expressed in output unit"""
        # lazy_init not required here because this method is called by a method with the @lazy_init
        log_debug(self, f"{self.name} _set_value to {value}")
        self._controller.set_output_value(self, value)

    @lazy_init
    def _start_ramping(self, value):
        """Start the ramping process to target_value"""
        # lazy_init not required here because this method is called by a method with the @lazy_init
        log_debug(self, f"{self.name} _start_ramping to {value}")
        try:
            self._use_soft_ramp = False
            self._controller.start_output_ramp(self, value)
        except NotImplementedError:
            self._use_soft_ramp = True
            self._ramp.start(value)

    @lazy_init
    def _stop_ramping(self):
        """Stop the ramping process"""
        log_debug(self, f"{self.name} _stop_ramping")
        if (
            self._use_soft_ramp is None
        ):  # case where '_start_ramping' was never called previously.
            try:
                self._controller.stop_output_ramp(self)
            except NotImplementedError:
                pass
        elif self._use_soft_ramp:
            self._ramp.stop()
        else:
            self._controller.stop_output_ramp(self)

    def _scale_pid_output(self, pid_range, pid_output):
        """Convert the pid output value into a value expressed in output units"""

        xmin, xmax = pid_range
        ymin, ymax = self.limits

        if None in (ymin, ymax) or (ymin == ymax):
            return pid_output
        else:
            a = (ymax - ymin) / (xmax - xmin)
            b = ymin - a * xmin
            return pid_output * a + b


class ExternalOutput(Output):
    """Implements the access to an external output device (i.e. not accessed via
    the regulation controller itself, like an axis)

    Managed devices are objects of the type:

    - Axis

    The Output has a ramp object.
    If `ramprate != 0` then any new value sent to the output
    will use a ramp to reach that value (hardware ramping if available, else a
    software ramp).
    """

    def __init__(self, config):
        super().__init__(None, config)

        self.device = config.get("device")
        self.mode = config.get("mode", "absolute")
        self.load_base_config()

        self._controller = self._scc

    # ----------- BASE METHODS -----------------------------------------

    @property
    def ramprate(self):
        """Get ramprate (in output unit per second)"""
        value = self._ramp.rate
        log_debug(self, f"{self.name} ramprate = {value}")
        return value

    @ramprate.setter
    def ramprate(self, value):
        """Set ramprate (in output unit per second)"""
        log_debug(self, f"{self.name} ramprate set to {value}")
        self._ramp.rate = value

    def is_ramping(self):
        """
        Get the ramping status.
        """
        value = self._ramp.is_ramping()
        log_debug(self, f"{self.name} is_ramping = {value}")
        return value

    def _start_ramping(self, value):
        """Start the ramping process to target_value"""
        log_debug(self, f"{self.name} _start_ramping to {value}")
        self._ramp.start(value)

    def _stop_ramping(self):
        """Stop the ramping process"""
        log_debug(self, f"{self.name} _stop_ramping")
        self._ramp.stop()

    # ----------- METHODS THAT A CHILD CLASS MAY CUSTOMIZE ------------------

    def __info__(self):
        lines = ["\n"]
        lines.append(f"=== ExternalOutput: {self.name} ===")
        lines.append(f"device: {_get_external_device_name(self.device)}")
        lines.append(
            f"current value: {self.read():.3f} {self.config.get('unit', 'N/A')}"
        )
        lines.append("\n=== Output.set_value ramping options ===")
        lines.append(f"ramprate: {self.ramprate}")
        lines.append(f"ramping: {self.is_ramping()}")
        lines.append(f"limits: {self._limits}")
        return "\n".join(lines)

    def state(self):
        """Return the state of the output device"""
        if isinstance(self.device, Axis):
            value = self.device.state
        else:
            raise TypeError("the associated device must be an 'Axis'")
        log_debug(self, f"{self.name} state = {value}")
        return value

    def read(self):
        """Return the current value of the output device (in output unit)"""
        if isinstance(self.device, Axis):
            value = self.device.position
        else:
            raise TypeError("the associated device must be an 'Axis'")
        log_debug(self, f"{self.name} read = {value}")
        return value

    def _set_value(self, value):
        """Set the value for the output. Value is expressed in output unit"""
        log_debug(self, f"{self.name} _set_value to {value}")
        if isinstance(self.device, Axis):
            self.device.move(value)
        else:
            raise TypeError("the associated device must be an 'Axis'")

    def _scale_pid_output(self, pid_range, pid_output):
        """Convert the pid output value into a value expressed in output units"""

        if self.mode == "relative" and isinstance(self.device, Axis):
            # in case of an axis and relative mode, consider pid_output value as a relative quantity
            return pid_output + self.device._set_position
        else:
            return super()._scale_pid_output(pid_range, pid_output)


@with_custom_members
class Loop(CounterContainer):
    """Implements the access to the regulation loop

    The regulation is the PID process that:
    1) reads a value from an input device.
    2) takes a target value (setpoint) and compare it to the current input value (processed value).
    3) computes an output value  and send it to an output device which has an effect on the processed value.
    4) back to step 1) and loop forever so that the processed value reaches the target value and stays stable around that target value.

    The Loop has:
    -one input: an Input object to read the processed value (ex: temperature sensor).
    -one output: an Output object which has an effect on the processed value (ex: cooling device).

    The regulation is automatically started by setting a new setpoint (Loop.setpoint = target_value).
    The Loop object implements methods to manage the PID algorithm that performs the regulation.
    A Loop object is associated to one Input and one Output.

    The Loop has a ramp object. If loop.ramprate != 0 then any new setpoint cmd
    will use a ramp to reach that value (HW if available else a soft_ramp).

    The loop output has a ramp object. If loop.output.ramprate != 0 then any new value sent to the output
    will use a ramp to reach that value (HW if available else a soft_ramp).

    """

    @enum.unique
    class WaitMode(enum.IntEnum):
        RAMP = 1
        DEADBAND = 2

    def __init__(self, controller, config):
        """Constructor"""

        self._name = config["name"]

        self._controller = controller
        self._config = config
        self._channel = self._config.get("channel")
        self._input = config.get("input")
        self._output = config.get("output")

        self._ramp = SoftRamp(self.input.read, self._set_setpoint)
        self._use_soft_ramp = None
        self._force_ramping_from_current_pv = config.get("ramp_from_pv", False)

        # useful attribute for a temperature controller writer
        self._attr_dict = {}

        self._last_setpoint = None
        self._deadband = 0.1
        self._deadband_time = 1.0
        self._deadband_idle_factor = 0.5
        self._in_deadband = False
        self._time_enter_deadband = None

        self._axis_in_motion = False

        self._wait_mode = self.WaitMode.DEADBAND  # RAMP

        self.reg_plot = None

        self._create_soft_axis()

        self._scc = SCC(self.name, self)
        if self.controller is None:
            msf = self.config.get("max_sampling_frequency", 1)
        else:
            msf = self.controller.config.get("max_sampling_frequency", 1)
        self._scc.max_sampling_frequency = msf
        self._scc.create_counter(
            SamplingCounter, self.name, unit=self.config.get("unit"), mode="SINGLE"
        )

        global_map.register(self, parents_list=["counters"])

    def __del__(self):
        self.close()

    def __close__(self):
        self.close()

    def close(self):

        if self.reg_plot:
            self.reg_plot.stop()

        if self._ramp:
            self._ramp.stop()

    # ----------- BASE METHODS -----------------------------------------

    @lazy_init
    def read(self):
        """Return the current working setpoint"""
        value = self._get_working_setpoint()
        log_debug(self, f"{self.name} read = {value}")
        return value

    @property
    def name(self):
        return self._name

    ##--- CONFIG METHODS
    def load_base_config(self):
        """Load from the config the values of the standard parameters"""

        self.deadband = self._config.get("deadband", 0.1)
        self.deadband_time = self._config.get("deadband_time", 1.0)
        self.deadband_idle_factor = self._config.get("deadband_idle_factor", 0.5)

        if self._config.get("wait_mode") is not None:
            self.wait_mode = self._config.get("wait_mode")

        # below the parameters that may requires communication with the controller

        if self._config.get("P") is not None:
            self.kp = self._config.get("P")
        if self._config.get("I") is not None:
            self.ki = self._config.get("I")
        if self._config.get("D") is not None:
            self.kd = self._config.get("D")

        if self._config.get("frequency") is not None:
            self.sampling_frequency = self._config.get("frequency")

        if self._config.get("ramprate") is not None:
            self.ramprate = self._config.get("ramprate")

        if (self._config.get("low_limit") is not None) and (
            self._config.get("high_limit") is not None
        ):
            self.pid_range = (
                self._config.get("low_limit"),
                self._config.get("high_limit"),
            )

    ##--- MAIN ATTRIBUTES
    @autocomplete_property
    def controller(self):
        """Return the associated regulation controller"""

        return self._controller

    @property
    def config(self):
        """Return the loop config"""

        return self._config

    @property
    def channel(self):
        return self._channel

    @autocomplete_property
    def counters(self):
        # return self._scc.counters
        """Standard counter namespace"""

        all_counters = (
            list(self.input.counters)
            + list(self.output.counters)
            + [self._scc.counters[self.name]]
        )

        return counter_namespace(all_counters)

    @autocomplete_property
    def input(self):
        """Return the input object"""

        return self._input

    @autocomplete_property
    def output(self):
        """Return the output object"""

        return self._output

    @autocomplete_property
    def soft_ramp(self):
        """Get the software ramp object"""

        return self._ramp

    ##--- DEADBAND METHODS
    @property
    def deadband(self):
        """Get the deadband value (in input unit).

        The regulation is considered stable if input value is in the range: setpoint +/- deadband
        for a time >= deadband_time.
        """
        value = self._deadband
        log_debug(self, f"{self.name} deadband = {value}")
        return value

    @deadband.setter
    def deadband(self, value):
        """Set the deadband value (in input unit).

        The regulation is considered stable if input value is in the range: setpoint +/- deadband
        for a time >= deadband_time.
        """
        log_debug(self, f"{self.name} deadband set to {value}")
        self._deadband = value
        self._soft_axis._Axis__tolerance = value

    @property
    def deadband_time(self):
        """Get the deadband_time value (s).

        The regulation is considered stable if input value is in the range: setpoint +/- deadband
        for a time >= deadband_time.
        """
        value = self._deadband_time
        log_debug(self, f"{self.name} deadband_time = {value}")
        return value

    @deadband_time.setter
    def deadband_time(self, value):
        """Set the deadband_time value (s).

        The regulation is considered stable if input value is in the range: setpoint +/- deadband
        for a time >= deadband_time.
        """

        log_debug(self, f"{self.name} deadband_time set to {value}")
        self._deadband_time = value

    @property
    def deadband_idle_factor(self):
        """Get the deadband_idle_factor value.

        The regulation (PID process) won't send a command to the Output if the
        processed value is in the range: setpoint +/- deadband_idle_factor*deadband.
        """
        value = self._deadband_idle_factor
        log_debug(self, f"{self.name} deadband_idle_factor = {value}")
        return value

    @deadband_idle_factor.setter
    def deadband_idle_factor(self, value):
        """Set the deadband_idle_factor value (range = [0, 1]).

        The regulation (PID process) won't send a command to the Output if the
        processed value is in the range: setpoint +/- deadband_idle_factor*deadband.
        """

        log_debug(self, f"{self.name} deadband_idle_factor set to {value}")
        value = max(min(value, 1), 0)
        self._deadband_idle_factor = value

    def is_in_deadband(self):
        return self._x_is_in_deadband(self.input.read())

    def is_in_idleband(self):
        return self._x_is_in_idleband(self.input.read())

    def _get_last_input_value(self):
        return self.input.read()

    def _get_last_output_value(self):
        return self.output.read()

    ##--- CTRL METHODS
    @property
    def setpoint(self):
        """
        Get the current setpoint (target value) (in input unit)
        """
        value = self._get_setpoint()
        log_debug(self, f"{self.name} setpoint = {value}")
        return value

    @setpoint.setter
    def setpoint(self, value):
        """
        Set the new setpoint (target value, in input unit) and start regulation process (if not running already) (w/wo ramp) to reach this setpoint
        """
        log_debug(self, f"{self.name} setpoint set to {value}")
        self._in_deadband = False  # see self.axis_state()
        self._start_regulation(value)
        self._start_ramping(value)
        self._last_setpoint = value

    def stop(self):
        """Stop the ramping"""
        log_debug(self, f"{self.name} stop")
        self._stop_ramping()

    def abort(self):
        """Stop the ramping (alias for stop)"""
        log_debug(self, f"{self.name} abort")
        self._stop_ramping()

    ##--- SOFT AXIS METHODS: makes the Loop object scannable (ex: ascan(loop, ...) )

    @autocomplete_property
    def axis(self):
        """Return a SoftAxis object that makes the Loop scanable"""

        return self._soft_axis

    def axis_position(self):
        """Return the input device value as the axis position"""

        return self.input.read()

    def axis_move(self, pos):
        """Set the Loop setpoint value as if moving an axis to a position"""

        self._axis_in_motion = True
        self.setpoint = pos

    def axis_stop(self):
        """Set the setpoint to the current input device value as if stopping a move on an axis"""

        self._stop_ramping()
        self._axis_in_motion = False

    def axis_state(self):
        """Return the current state of the Loop as if it was an axis.

        Two modes (self._wait_mode) are available, the 'ramp' mode or the 'deadband' mode.

        - In 'ramp' mode, the axis is 'MOVING' if a ramping process is alive,
          else it is considered as 'READY'.

        - In 'deadband' mode, the axis is 'READY' if the current input value is
          in the deadband interval for a time >= 'deadband_time',
          else it is considered as 'MOVING'.

        """

        # "READY": "Axis is READY",
        # "MOVING": "Axis is MOVING",
        # "FAULT": "Error from controller",
        # "LIMPOS": "Hardware high limit active",
        # "LIMNEG": "Hardware low limit active",
        # "HOME": "Home signal active",
        # "OFF": "Axis power is off",
        # "DISABLED": "Axis cannot move",

        if self._wait_mode == self.WaitMode.RAMP:
            if self.is_ramping():
                return AxisState("MOVING")
            else:
                self._axis_in_motion = False
                return AxisState("READY")

        else:

            if not self._axis_in_motion:
                return AxisState("READY")

            # NOT IN DEADBAND
            if not self.is_in_deadband():
                self._time_enter_deadband = None
                self._in_deadband = False
                return AxisState("MOVING")

            # IN DEADBAND
            else:

                if not self._in_deadband:
                    self._in_deadband = True
                    self._time_enter_deadband = time.perf_counter()
                    return AxisState("MOVING")

                else:
                    dt = time.perf_counter() - self._time_enter_deadband
                    if dt >= self.deadband_time:
                        self._axis_in_motion = False
                        return AxisState("READY")
                    else:
                        return AxisState("MOVING")

    @property
    def wait_mode(self):
        """Get the waiting mode.

        It is used during a scan to determine if the regulation as reached a scan point (see scan 'READY' state).
        <WaitMode.RAMP    : 1>  : READY when the loop has finished to ramp to the scan point.
        <WaitMode.DEADBAND: 2>  : READY when the processed value is in the 'deadband' around the scan point for a time >= 'deadband_time'.
        """
        value = self._wait_mode
        log_debug(self, f"{self.name} wait_mode = {value}")
        return value

    @wait_mode.setter
    def wait_mode(self, value):
        """Set the waiting mode.

        It is used during a scan to determine if the regulation as reached a scan point (see scan 'READY' state).
        <WaitMode.RAMP    : 1>  : READY when the loop has finished to ramp to the scan point.
        <WaitMode.DEADBAND: 2>  : READY when the processed value is in the 'deadband' around the scan point for a time >= 'deadband_time'.
        """
        log_debug(self, f"{self.name} wait_mode set to {value}")
        if isinstance(value, int):
            self._wait_mode = self.WaitMode(value)
        elif isinstance(value, str):
            if value.lower() in ["deadband", "2"]:
                self._wait_mode = self.WaitMode(2)
            else:
                self._wait_mode = self.WaitMode(1)

    # ----------- METHODS THAT A CHILD CLASS MAY CUSTOMIZE ------------------

    @lazy_init
    def __info__(self):
        lines = ["\n"]
        lines.append(f"=== Loop: {self.name} ===")
        lines.append(
            f"controller: {self.controller.name if self.controller.name is not None else self.controller.__class__.__name__}"
        )
        lines.append(
            f"Input: {self.input.name} @ {self.input.read():.3f} {self.input.config.get('unit', 'N/A')}"
        )
        lines.append(
            f"output: {self.output.name} @ {self.output.read():.3f} {self.output.config.get('unit', 'N/A')}"
        )

        lines.append("\n=== Setpoint ===")
        lines.append(
            f"setpoint: {self.setpoint} {self.input.config.get('unit', 'N/A')}"
        )
        lines.append(
            f"ramprate: {self.ramprate} {self.input.config.get('unit', 'N/A')}/{self.config.get('ramprate_time_unit', 'min')}"
        )
        lines.append(f"ramping: {self.is_ramping()}")
        lines.append("\n=== PID ===")
        lines.append(f"kp: {self.kp}")
        lines.append(f"ki: {self.ki}")
        lines.append(f"kd: {self.kd}")

        return "\n".join(lines)

    @property
    @lazy_init
    def kp(self):
        """
        Get the P value (for PID)
        """
        value = self._controller.get_kp(self)
        log_debug(self, f"{self.name} kp = {value}")
        return value

    @kp.setter
    @lazy_init
    def kp(self, value):
        """
        Set the P value (for PID)
        """
        log_debug(self, f"{self.name} kp set to {value}")
        self._controller.set_kp(self, value)

    @property
    @lazy_init
    def ki(self):
        """
        Get the I value (for PID)
        """
        value = self._controller.get_ki(self)
        log_debug(self, f"{self.name} ki = {value}")
        return value

    @ki.setter
    @lazy_init
    def ki(self, value):
        """
        Set the I value (for PID)
        """
        log_debug(self, f"{self.name} ki set to {value}")
        self._controller.set_ki(self, value)

    @property
    @lazy_init
    def kd(self):
        """
        Get the D value (for PID)
        """
        value = self._controller.get_kd(self)
        log_debug(self, f"{self.name} kd = {value}")
        return value

    @kd.setter
    @lazy_init
    def kd(self, value):
        """
        Set the D value (for PID)
        """
        log_debug(self, f"{self.name} kd set to {value}")
        self._controller.set_kd(self, value)

    @property
    @lazy_init
    def sampling_frequency(self):
        """
        Get the sampling frequency (PID) [Hz]
        """
        value = self._controller.get_sampling_frequency(self)
        log_debug(self, f"{self.name} sampling_frequency = {value}")
        return value

    @sampling_frequency.setter
    @lazy_init
    def sampling_frequency(self, value):
        """
        Set the sampling frequency (PID) [Hz]
        """
        log_debug(self, f"{self.name} sampling_frequency set to {value}")
        self._controller.set_sampling_frequency(self, value)

    @property
    @lazy_init
    def pid_range(self):
        """
        Get the PID range (PID output value limits).

        Usually, the PID range must be:
        - [ 0, 1] for uni-directional 'moves' on the output (like heating more or less)
        - [-1, 1] for bi-directional 'moves' on the output (like heating/cooling or relative moves with a motor axis).

        The PID value is the value returned by the PID algorithm.
        """
        value = self._controller.get_pid_range(self)
        log_debug(self, f"{self.name} pid_range = {value}")
        return value

    @pid_range.setter
    @lazy_init
    def pid_range(self, value):
        """
        Set the PID range (PID output value limits).

        Usually, the PID range must be:
        - [ 0, 1] for uni-directional 'moves' on the output (like heating more or less)
        - [-1, 1] for bi-directional 'moves' on the output (like heating/cooling or relative moves with a motor axis).

        The PID value is the value returned by the PID algorithm.
        """
        log_debug(self, f"{self.name} pid_range set to {value}")
        self._controller.set_pid_range(self, value)

    @property
    @lazy_init
    def ramprate(self):
        """Get ramprate (in input unit per second)"""
        try:
            value = self._controller.get_ramprate(self)
        except NotImplementedError:
            value = self._ramp.rate
        log_debug(self, f"{self.name} ramprate = {value}")
        return value

    @ramprate.setter
    @lazy_init
    def ramprate(self, value):
        """Set ramprate (in input unit per second)"""
        log_debug(self, f"{self.name} ramprate set to {value}")
        self._ramp.rate = value
        try:
            self._controller.set_ramprate(self, value)
        except NotImplementedError:
            pass

    @lazy_init
    def is_ramping(self):
        """
        Get the ramping status.
        """
        if (
            self._use_soft_ramp is None
        ):  # case where '_start_ramping' was never called previously.
            try:
                value = self._controller.is_ramping(self)
            except NotImplementedError:
                value = False

        elif self._use_soft_ramp:

            value = self._ramp.is_ramping()

        else:
            value = self._controller.is_ramping(self)

        log_debug(self, f"{self.name} is_ramping = {value}")
        return value

    # ------------------------------------------------------
    @lazy_init
    def _get_working_setpoint(self):
        """Get the current working setpoint"""

        if self._use_soft_ramp:
            value = self._ramp._wrk_setpoint
        else:
            try:
                value = self._controller.get_working_setpoint(self)
            except NotImplementedError:
                # _get_working_setpoint can be polled by counting or plot
                # so cache the value if the controller can only returns the target setpoint
                # which is constant and then doesn't need to be re-read.
                if self._last_setpoint is None:
                    self._last_setpoint = self._get_setpoint()
                value = self._last_setpoint

        log_debug(self, f"{self.name} _get_working_setpoint = {value}")
        return value

    @lazy_init
    def _get_setpoint(self):
        """Get the current setpoint"""
        value = self._controller.get_setpoint(self)
        log_debug(self, f"{self.name} _get_setpoint = {value}")
        return value

    @lazy_init
    def _set_setpoint(self, value):
        """Set the current setpoint"""
        log_debug(self, f"{self.name} _set_setpoint to {value}")
        self._controller.set_setpoint(self, value)

    @lazy_init
    def _start_regulation(self, sp):
        """Start the regulation loop"""
        log_debug(self, f"{self.name} _start_regulation")
        self._controller.start_regulation(self)

    @lazy_init
    def _stop_regulation(self):
        """Stop the regulation loop"""
        log_debug(self, f"{self.name} _stop_regulation")
        self._controller.stop_regulation(self)

    @lazy_init
    def _start_ramping(self, value):
        """Start the ramping to setpoint value"""
        log_debug(self, f"{self.name} _start_ramping to {value}")
        try:
            self._use_soft_ramp = False

            if self._force_ramping_from_current_pv:
                current_value = self.input.read()
                if not self._x_is_in_deadband(current_value):
                    self._set_setpoint(current_value)

            self._controller.start_ramp(self, value)

        except NotImplementedError:
            self._use_soft_ramp = True
            self._ramp.start(value)  # soft ramp starts from current PV

    @lazy_init
    def _stop_ramping(self):
        """Stop the ramping"""
        log_debug(self, f"{self.name} _stop_ramping")
        if (
            self._use_soft_ramp is None
        ):  # case where '_start_ramping' was never called previously.
            try:
                self._controller.stop_ramp(self)
            except NotImplementedError:
                pass
        elif self._use_soft_ramp:
            self._ramp.stop()
        else:
            self._controller.stop_ramp(self)

    def plot(self):
        if not self.reg_plot:
            self.reg_plot = RegPlot(self)
        self.reg_plot.start()
        return self.reg_plot

    def _create_soft_axis(self):
        """Create a SoftAxis object that makes the Loop scanable"""

        name = self.name + "_axis"

        self._soft_axis = SoftAxis(
            name,
            self,
            position="axis_position",
            move="axis_move",
            stop="axis_stop",
            state="axis_state",
            low_limit=float("-inf"),
            high_limit=float("+inf"),
            tolerance=self.deadband,
            export_to_session=True,
        )

        self._soft_axis._unit = self.input.config.get("unit", "N/A")

    def _x_is_in_deadband(self, x):
        sp = self.setpoint
        if (x < sp - self.deadband) or (x > sp + self.deadband):
            return False
        else:
            return True

    def _x_is_in_idleband(self, x):
        sp = self.setpoint
        if (x < sp - self.deadband * self._deadband_idle_factor) or (
            x > sp + self.deadband * self._deadband_idle_factor
        ):
            return False
        else:
            return True


class SoftLoop(Loop):
    """Implements the software regulation loop.

    A SoftLoop should be used when there is no hardware to handle the PID regulation
    or when we want to override the internal PID regulation of the hardware.

    The regulation is the PID process that:
    1) reads a value from an input device.
    2) takes a target value (setpoint) and compare it to the current input value (processed value).
    3) computes an output value and send it to an output device which has an effect on the processed value.
    4) back to step 1) and loop forever so that the processed value reaches the target value and stays stable around that target value.

    The Loop has:
    -one input: an Input object to read the processed value (ex: temperature sensor).
    -one output: an Output object which has an effect on the processed value (ex: cooling device).

    The regulation is automatically started by setting a new setpoint (Loop.setpoint = target_value).
    The regulation is handled by the software and is based on the 'simple_pid' python module.

    The Loop has a ramp object. If loop.ramprate != 0 then any new setpoint cmd
    will use a ramp to reach that value (HW if available else a soft_ramp).

    The Output has a ramp object. If loop.output.ramprate != 0 then any new value sent to the output
    will use a ramp to reach that value (HW if available else a soft_ramp).

    """

    def __init__(self, config):
        super().__init__(None, config)

        self.pid = PID(
            Kp=1.0,
            Ki=0.0,
            Kd=0.0,
            setpoint=0.0,
            sample_time=0.01,
            output_limits=(0.0, 1.0),
            auto_mode=True,
            proportional_on_measurement=False,
        )

        self.task = None
        self._stop_event = gevent.event.Event()

        self._last_input_value = None
        self._last_output_value = None
        self._max_attempts_before_failure = None

        self.load_base_config()
        self.max_attempts_before_failure = config.get("max_attempts_before_failure", 3)

        self._controller = self._scc

    def __info__(self):
        lines = ["\n"]
        lines.append(f"=== SoftLoop: {self.name} ===")
        lines.append(
            f"Input: {self.input.name} @ {self.input.read():.3f} {self.input.config.get('unit', 'N/A')}"
        )
        lines.append(
            f"output: {self.output.name} @ {self.output.read():.3f} {self.output.config.get('unit', 'N/A')}"
        )
        lines.append(
            f"setpoint: {self.setpoint} {self.input.config.get('unit', 'N/A')}"
        )
        lines.append(
            f"ramp rate: {self.ramprate} {self.input.config.get('unit', 'N/A')}/s"
        )
        lines.append(f"ramping: {self.is_ramping()}")
        lines.append(f"deadband: {self.deadband} s")
        lines.append(f"deadband time: {self.deadband_time} s")
        lines.append(f"wait mode: {self.wait_mode}")
        lines.append(f"kp: {self.kp}")
        lines.append(f"ki: {self.ki}")
        lines.append(f"kd: {self.kd}")

        return "\n".join(lines)

    def close(self):
        super().close()
        self._stop_regulation()

    def read(self):
        """Return the current working setpoint"""
        value = self._get_working_setpoint()
        log_debug(self, f"{self.name} read = {value}")
        return value

    def reset(self):
        if isinstance(self.output, ExternalOutput) and self.output.mode == "relative":
            last_output = 0
        else:
            last_output = self.output.read()

        self.pid.set_auto_mode(False)
        self.pid.set_auto_mode(True, last_output)

    @property
    def max_attempts_before_failure(self):
        """
        Get the maximum number of read input/set output attempts before failure
        """

        return self._max_attempts_before_failure

    @max_attempts_before_failure.setter
    def max_attempts_before_failure(self, value):
        """
        Set the maximum number of read input/set output attempts before failure
        """

        if not isinstance(value, int) or value < 0:
            ValueError("max_attempts_before_failure should be a positive integer")
        self._max_attempts_before_failure = value

    @property
    def kp(self):
        """
        Get the P value (for PID)
        """
        value = self.pid.Kp
        log_debug(self, f"{self.name} kp = {value}")
        return value

    @kp.setter
    def kp(self, value):
        """
        Set the P value (for PID)
        """
        log_debug(self, f"{self.name} kp set to {value}")
        self.pid.Kp = value

    @property
    def ki(self):
        """
        Get the I value (for PID)
        """
        value = self.pid.Ki
        log_debug(self, f"{self.name} ki = {value}")
        return value

    @ki.setter
    def ki(self, value):
        """
        Set the I value (for PID)
        """
        log_debug(self, f"{self.name} ki set to {value}")
        self.pid.Ki = value

    @property
    def kd(self):
        """
        Get the D value (for PID)
        """
        value = self.pid.Kd
        log_debug(self, f"{self.name} kd = {value}")
        return value

    @kd.setter
    def kd(self, value):
        """
        Set the D value (for PID)
        """

        log_debug(self, f"{self.name} kd set to {value}")
        self.pid.Kd = value

    @property
    def sampling_frequency(self):
        """
        Get the sampling frequency (PID) [Hz]
        """
        value = 1.0 / self.pid.sample_time
        log_debug(self, f"{self.name} sampling_frequency = {value}")
        return value

    @sampling_frequency.setter
    def sampling_frequency(self, value):
        """
        Set the sampling frequency (PID) [Hz]
        """
        log_debug(self, f"{self.name} sampling_frequency set to {value}")
        self.pid.sample_time = 1.0 / value

    @property
    def pid_range(self):
        """
        Get the PID range (PID output value limits).

        Usually, the PID range must be:
        - [ 0, 1] for uni-directional 'moves' on the output (like heating more or less)
        - [-1, 1] for bi-directional 'moves' on the output (like heating/cooling or relative moves with a motor axis).

        The PID value is the value returned by the PID algorithm.
        """
        value = self.pid.output_limits
        log_debug(self, f"{self.name} pid_range = {value}")
        return value

    @pid_range.setter
    def pid_range(self, value):
        """
        Set the PID range (PID output value limits).

        Usually, the PID range must be:
        - [ 0, 1] for uni-directional 'moves' on the output (like heating more or less)
        - [-1, 1] for bi-directional 'moves' on the output (like heating/cooling or relative moves with a motor axis).

        The PID value is the value returned by the PID algorithm.
        """

        log_debug(self, f"{self.name} pid_range set to {value}")
        self.pid.output_limits = value[0:2]

    @property
    def ramprate(self):
        """Get ramprate (in input unit per second)"""
        value = self._ramp.rate
        log_debug(self, f"{self.name} ramprate = {value}")
        return value

    @ramprate.setter
    def ramprate(self, value):
        """Set ramprate (in input unit per second)"""
        log_debug(self, f"{self.name} ramprate set to {value}")
        self._ramp.rate = value

    def is_ramping(self):
        """
        Get the ramping status.
        """
        value = self._ramp.is_ramping()
        log_debug(self, f"{self.name} is_ramping = {value}")
        return value

    def is_regulating(self):
        """
        Get the regulation status.
        """
        value = bool(self.task)
        log_debug(self, f"{self.name} is_regulating = {value}")
        return value

    def apply_proportional_on_measurement(self, enable):
        """
        To eliminate overshoot in certain types of systems,
        the proportional term can be calculated directly on the measurement instead of the error.
        """
        log_debug(
            self, f"{self.name} apply_proportional_on_measurement set to {enable}"
        )
        self.pid.proportional_on_measurement = bool(enable)

    def _get_working_setpoint(self):
        """Get the current working setpoint"""
        # The SoftRamp updates the setpoint value of the SoftLoop while ramping
        # so working_setpoint and setpoint are the same
        value = self._get_setpoint()
        log_debug(self, f"{self.name} _get_working_setpoint = {value}")
        return value

    def _get_setpoint(self):
        """
        Get the current setpoint (target value) (in input unit)
        """
        value = self.pid.setpoint
        log_debug(self, f"{self.name} _get_setpoint = {value}")
        return value

    def _set_setpoint(self, value):
        """
        For internal use only! Users must use the property only: 'Loop.setpoint = xxx'
        Set the current setpoint (target value) (in input unit)
        """
        log_debug(self, f"{self.name} _set_setpoint to {value}")
        self.pid.setpoint = value

    def _start_regulation(self, sp):
        """Start the regulation loop"""
        log_debug(self, f"{self.name} _start_regulation")
        if not self.task:
            self._stop_event.clear()
            self.reset()
            self.pid.setpoint = sp
            self.task = gevent.spawn(self._do_regulation)

    def _stop_regulation(self):
        """Stop the regulation loop"""
        log_debug(self, f"{self.name} _stop_regulation")
        if self.task is not None:
            self._stop_event.set()
            with gevent.Timeout(2.0):
                self.task.join()

    def _start_ramping(self, value):
        """Start the ramping to setpoint value"""
        log_debug(self, f"{self.name} _start_ramping to {value}")
        self._ramp.start(value)  # soft ramp starts from current PV

    def _stop_ramping(self):
        """Stop the ramping"""
        log_debug(self, f"{self.name} _stop_ramping")
        self._ramp.stop()

    def _do_regulation(self):
        failures_in = 0
        failures_out = 0

        while not self._stop_event.is_set():

            if self.input.allow_regulation():

                try:
                    self._last_input_value = input_value = self.input.read()
                except Exception as e:
                    failures_in += 1
                    if failures_in > self.max_attempts_before_failure:
                        msg = f"{self.name}.{self.input.name}: too many attempts to read input value, regulation stopped"
                        log_error(self, msg)
                        raise TimeoutError(msg) from e
                else:
                    failures_in = 0
                    pid_output = self.pid(input_value)
                    output_value = self.output._scale_pid_output(
                        self.pid_range, pid_output
                    )

                    if not self._x_is_in_idleband(input_value):
                        try:
                            output_value = self.output.set_value(output_value)
                        except Exception as e:
                            failures_out += 1
                            if failures_out > self.max_attempts_before_failure:
                                msg = f"{self.name}.{self.output.name}: too many attempts to set output value, regulation stopped"
                                log_error(self, msg)
                                raise TimeoutError(msg) from e
                        else:
                            failures_out = 0
                            self._last_output_value = output_value

            gevent.sleep(self.pid.sample_time)

    def _get_last_input_value(self):
        # for an optimized _store_history_data (less com with input device)
        if self.is_regulating():
            if self._last_input_value is not None:
                return self._last_input_value
        return self.input.read()

    def _get_last_output_value(self):
        # for an optimized _store_history_data (less com with output device)
        if self.is_regulating():
            if self._last_output_value is not None:
                return self._last_output_value
        return self.output.read()


class SoftRamp:
    """Implements the ramping process associate"""

    def __init__(self, get_value_func, set_value_func):
        """Constructor

        - get_value_func: a callable that returns the current value of the variable to be ramped
        - set_value_func: a callable that sets the current value of the variable to be ramped

        """

        self._get_value_func = get_value_func
        self._set_value_func = set_value_func

        # useful attribute for a temperature controller writer
        self._attr_dict = {}

        # --- SOFT RAMP ATTRIBUTES -----

        self._rate = 0.0
        self._poll_time = 0.02

        self.target_value = None
        self.new_target_value = None
        self._wrk_setpoint = None

        self.task = None
        self._stop_event = gevent.event.Event()

        self.start_time = None
        self.start_value = None
        self.direction = None
        self.count = 0

    def __del__(self):
        self.stop()

    @property
    def poll_time(self):
        """Get the polling time (sleep time) used by the ramping task loop"""
        value = self._poll_time
        log_debug(self, f"{self} poll_time = {value}")
        return value

    @poll_time.setter
    def poll_time(self, value):
        """Set the polling time (sleep time) used by the ramping task loop"""
        log_debug(self, f"{self} poll_time set to {value}")
        self._poll_time = value

    @property
    def rate(self):
        """Get ramprate (in input unit per second)"""
        value = self._rate
        log_debug(self, f"{self} rate = {value}")
        return value

    @rate.setter
    def rate(self, value):
        """Set ramprate (in input unit per second)"""
        log_debug(self, f"{self} rate set to {value}")
        restart = False
        if self.is_ramping():
            self.stop()
            self.target_value = None
            restart = True

        self._rate = value

        if restart:
            self.start(self.new_target_value)

    def start(self, value):
        """Start the ramping process to target_value"""
        log_debug(self, f"{self} start to {value}")
        self.new_target_value = value
        if not self._rate:
            self._set_working_point(self.new_target_value)
        elif not self.task:
            self._stop_event.clear()
            self.task = gevent.spawn(self._do_ramping)

    def stop(self):
        """Stop the ramping process"""
        log_debug(self, f"{self} stop")
        if self.task is not None:
            self._stop_event.set()
            with gevent.Timeout(2.0):
                self.task.join()

    def is_ramping(self):
        """
        Get the ramping status.
        """
        value = bool(self.task)
        log_debug(self, f"{self} is_ramping = {value}")
        return value

    def _set_working_point(self, value):
        """Set the intermediate point (during a ramping process)."""
        log_debug(self, f"{self} _set_working_point to {value}")
        self._wrk_setpoint = value
        self._set_value_func(value)

    def _calc_ramp(self):
        """Computes the ramp line (start_value, target_value, direction)"""

        if self.new_target_value != self.target_value:

            self.start_time = time.perf_counter()
            self.start_value = self._get_value_func()

            if self.new_target_value >= self.start_value:
                self.direction = 1
            else:
                self.direction = -1

            self.target_value = self.new_target_value

    def _do_ramping(self):
        """Performs the step by step ramping"""

        while not self._stop_event.is_set():

            self._calc_ramp()

            gevent.sleep(self._poll_time)

            if (
                self._rate == 0
            ):  # DEALS WITH THE CASE WHERE THE USER SET THE RAMPRATE TO ZERO WHILE A RUNNING RAMP HAS BEEN STARTED WITH A RAMPRATE!=0
                self._set_working_point(self.target_value)
                break
            else:
                dt = time.perf_counter() - self.start_time
                value = self.start_value + self.direction * self._rate * dt

            if self.direction == 1 and value <= self.target_value:

                self._set_working_point(value)

            elif self.direction == -1 and value >= self.target_value:

                self._set_working_point(value)

            else:
                self._set_working_point(self.target_value)
                break


class RegPlot:
    """A plotting tool for the regulation Loop.

    Plots the regulation Loop parameters over time (input_value, setpoint_value, output_value)
    Based on flint/silx modules

    usage:  plt = RegPlot( myloop )
            plt.start()
            ...
            plt.stop()
    """

    def __init__(self, tloop):

        self.loop = tloop

        self.task = None
        self._stop_event = gevent.event.Event()
        self.sleep_time = 0.1
        self.fig = None

    def __del__(self):
        self.stop()

    def create_plot(self):

        # Declare and setup the plot
        self.fig = get_flint().get_plot(
            plot_class="TimeCurvePlot",
            name=self.loop.name,
            unique_name=f"regul_plot_{self.loop.name}",
            closeable=True,
            selected=True,
        )
        self.fig.submit(
            "setGraphYLabel",
            f"Processed value ({self.loop.input.config.get('unit','')})",
        )
        self.fig.submit(
            "setGraphYLabel",
            f"Output ({self.loop.output.config.get('unit','')})",
            axis="right",
        )
        self.fig.submit("setGraphGrid", which=True)

        # Define the plot content
        self.fig.add_time_curve_item("setpoint", color="blue", linestyle="-", z=2)
        self.fig.add_time_curve_item("input", color="red", linestyle="-", z=2)
        self.fig.add_time_curve_item(
            "output", color="green", linestyle="-", yaxis="right", z=2
        )
        self.fig.add_time_curve_item("deadband_high", color="blue", linestyle="--", z=2)
        self.fig.add_time_curve_item("deadband_low", color="blue", linestyle="--", z=2)

    def is_plot_active(self):
        if self.fig is None:
            return False
        else:
            return self.fig.is_open()

    def start(self):
        if not self.is_plot_active():
            self.create_plot()

        if not self.task:
            self._stop_event.clear()
            self.task = gevent.spawn(self.run)

    def stop(self):
        if self.task is not None:
            self._stop_event.set()
            with gevent.Timeout(2.0):
                self.task.join()

    def run(self):
        while not self._stop_event.is_set():

            try:
                if not self.is_plot_active():
                    return
            except (gevent.timeout.Timeout, Exception):
                pass

            try:
                loop = self.loop
                data_time = time.time()
                setpoint = loop._get_working_setpoint()
                input_value = loop._get_last_input_value()
                output_value = loop._get_last_output_value()
                dbp = setpoint + loop.deadband
                dbm = setpoint - loop.deadband

                # Update curves plot (refreshes the plot widget)
                self.fig.append_data(
                    time=[data_time],
                    input=[input_value],
                    output=[output_value],
                    setpoint=[setpoint],
                    deadband_high=[dbp],
                    deadband_low=[dbm],
                )

            except (gevent.timeout.Timeout, Exception):
                log_debug(self, "Error while plotting the data", exc_info=True)

            gevent.sleep(self.sleep_time)
