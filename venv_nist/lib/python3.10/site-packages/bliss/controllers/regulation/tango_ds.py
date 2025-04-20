# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import numpy

from bliss.common.soft_axis import SoftAxis
from bliss.common.axis.state import AxisState
from bliss.common.tango import DeviceProxy
from bliss.common.regulation import ExternalInput, ExternalOutput, Loop, RegPlot


class TangoInput(ExternalInput):
    def __init__(self, config):

        device = DeviceProxy(config["tango_name"])

        cfg = {
            "device": device,
            "name": config.get("name", device.target_name),
            "channel": device.target_channel,
            "unit": device.target_unit,
            "mode": device.target_mode,
        }

        super().__init__(cfg)

    # ----------- METHODS THAT A CHILD CLASS MAY CUSTOMIZE ------------------

    def __info__(self):
        return self.device.target_info

    def read(self):
        return self.device.target_read

    def state(self):
        return self.device.target_state

    def allow_regulation(self):
        return self.device.allow_regulation


class TangoOutput(ExternalOutput):
    def __init__(self, config):

        device = DeviceProxy(config["tango_name"])

        cfg = {
            "device": device,
            "name": config.get("name", device.target_name),
            "channel": device.target_channel,
            "unit": device.target_unit,
            "mode": device.target_mode,
        }

        super().__init__(cfg)

    def __info__(self):
        return self.device.target_info

    def read(self):
        return self.device.target_read

    def state(self):
        return self.device.target_state

    def set_value(self, value):
        self.device.set_value = float(value)

    @property
    def limits(self):
        llow = self.device.limit_low
        lhigh = self.device.limit_high
        if numpy.isnan(llow):
            llow = None
        if numpy.isnan(lhigh):
            lhigh = None
        return [llow, lhigh]

    @limits.setter
    def limits(self, value):
        self.device.limit_low = value[0]
        self.device.limit_high = value[1]

    @property
    def ramprate(self):
        return self.device.ramprate

    @ramprate.setter
    def ramprate(self, value):
        self.device.ramprate = float(value)

    def is_ramping(self):
        return self.device.is_ramping

    def _start_ramping(self, value):
        raise NotImplementedError

    def _stop_ramping(self):
        raise NotImplementedError

    def _set_value(self, value):
        raise NotImplementedError

    @property
    def range(self):
        return self.device.range

    @range.setter
    def range(self, value):
        self.device.range = str(value)


class TangoLoop(Loop):
    def __init__(self, config):

        self.reg_plot = None
        self._ramp = None

        device = DeviceProxy(config["tango_name"])

        input_tango_name = device.target_input_device_name
        if not input_tango_name:
            msg = "cannot find associated input, check that tango database has a registered"
            msg += f" Input device with beacon_name = '{device.target_input_name}'"
            raise ValueError(msg)

        output_tango_name = device.target_output_device_name
        if not output_tango_name:
            msg = "cannot find associated output, check that tango database has a registered"
            msg += f" Output device with beacon_name = '{device.target_output_name}'"
            raise ValueError(msg)

        input_obj = TangoInput({"tango_name": input_tango_name})
        output_obj = TangoOutput({"tango_name": output_tango_name})

        cfg = {
            "device": device,
            "name": config.get("name", device.target_name),
            "channel": device.target_channel,
            "input": input_obj,
            "output": output_obj,
            "ramp_from_pv": device.ramp_from_pv,
            "deadband": device.deadband,
            "deadband_time": device.deadband_time,
            "wait_mode": device.wait_mode,
            "P": device.kp,
            "I": device.ki,
            "D": device.kd,
            "ramprate": device.ramprate,
            "unit": device.target_unit,
            "mode": device.target_mode,
        }

        self.device = device

        super().__init__(None, cfg)

        self._controller = self._scc

    def __info__(self):
        return self.device.target_info

    def read(self):
        return self.device.target_read

    def state(self):
        return self.device.target_state

    ##--- DEADBAND METHODS
    @property
    def deadband(self):
        return self.device.deadband

    @deadband.setter
    def deadband(self, value):
        self.device.deadband = float(value)

    @property
    def deadband_time(self):
        return self.device.deadband_time

    @deadband_time.setter
    def deadband_time(self, value):
        self.device.deadband_time = float(value)

    @property
    def deadband_idle_factor(self):
        return self.device.deadband_idle_factor

    @deadband_idle_factor.setter
    def deadband_idle_factor(self, value):
        self.device.deadband_idle_factor = float(value)

    def is_in_deadband(self):
        return self.device.is_in_deadband

    def is_in_idleband(self):
        return self.device.is_in_idleband

    ##--- CTRL METHODS
    @property
    def setpoint(self):
        return self.device.setpoint

    @setpoint.setter
    def setpoint(self, value):
        self.device.setpoint = float(value)

    def stop(self):
        self.device.stop()

    def abort(self):
        self.device.abort()

    ##--- SOFT AXIS METHODS: makes the Loop object scannable (ex: ascan(loop, ...) )

    def axis_position(self):
        return self.device.axis_position

    def axis_move(self, pos):
        self.device.axis_position = float(pos)

    def axis_stop(self):
        self.device.axis_stop()

    def axis_state(self):
        state = self.device.axis_state.strip().split()[0]
        return AxisState(state)

    @property
    def wait_mode(self):
        return self.device.wait_mode.split(".")[1]

    @wait_mode.setter
    def wait_mode(self, value):
        self.device.wait_mode = str(value)

    @property
    def kp(self):
        return self.device.kp

    @kp.setter
    def kp(self, value):
        self.device.kp = float(value)

    @property
    def ki(self):
        return self.device.ki

    @ki.setter
    def ki(self, value):
        self.device.ki = float(value)

    @property
    def kd(self):
        return self.device.kd

    @kd.setter
    def kd(self, value):
        self.device.kd = float(value)

    @property
    def sampling_frequency(self):
        return self.device.sampling_frequency

    @sampling_frequency.setter
    def sampling_frequency(self, value):
        self.device.sampling_frequency = float(value)

    @property
    def pid_range(self):
        low = self.device.pid_range_low
        high = self.device.pid_range_high
        if numpy.isnan(low):
            low = None
        if numpy.isnan(high):
            high = None
        return [low, high]

    @pid_range.setter
    def pid_range(self, value):
        self.device.pid_range_low = float(value[0])
        self.device.pid_range_high = float(value[1])

    @property
    def ramprate(self):
        return self.device.ramprate

    @ramprate.setter
    def ramprate(self, value):
        self.device.ramprate = float(value)

    def is_ramping(self):
        return self.device.is_ramping

    def is_regulating(self):
        return self.device.is_regulating

    @property
    def mode(self):
        return self.device.mode

    @mode.setter
    def mode(self, value):
        self.device.mode = str(value)

    # ------------------------------------------------------
    def _get_working_setpoint(self):
        return self.device.working_setpoint

    def _get_setpoint(self):
        raise NotImplementedError

    def _set_setpoint(self, value):
        raise NotImplementedError

    def _start_regulation(self):
        self.device.start_regulation()

    def _stop_regulation(self):
        self.device.stop_regulation()

    def _start_ramping(self, value):
        self.device.start_ramping(value)

    def _stop_ramping(self):
        self.device.stop_ramping()

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
        raise NotImplementedError

    def _x_is_in_idleband(self, x):
        raise NotImplementedError
