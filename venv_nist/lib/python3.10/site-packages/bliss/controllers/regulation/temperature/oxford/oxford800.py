# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
  - class: oxford800
    plugin: regulation
    module: temperature.oxford.oxford800
    cryoname: id10oxford800

    inputs:
        - name: ox_in
    outputs:
        - name: ox_out
    ctrl_loops:
        - name: ox_loop
          input: $ox_in
          output: $ox_out
          ramprate: 350   # (optional) default/starting ramprate [K/hour]
"""
import time
from functools import wraps
from gevent import sleep
import liboxford800
from bliss import global_map
from .oxford700 import Oxford700


def get_cryo(func):
    """Get the cryostream handler."""

    @wraps(func)
    def f(self, *args, **kwargs):
        cryo = liboxford800.get_handle(self._cryoname)
        if cryo is None:
            raise RuntimeError("Could not find oxford cryostream %r" % self._cryoname)
        return func(self, cryo, *args, **kwargs)

    return f


class StatusPacket:
    RUNMODE_CODES = [
        "StartUp",
        "StartUpFail",
        "StartUpOK",
        "Run",
        "SetUp",
        "ShutdownOK",
        "ShutdownFail",
    ]

    PHASE_CODES = [
        "Ramp",
        "Cool",
        "Plat",
        "Hold",
        "End",
        "Purge",
        "DeletePhase",
        "LoadProgram",
        "SaveProgram",
        "Soak",
        "Wait",
    ]

    ALARM_CODES = [
        "No errors or warnings",
        "Stop pressed",
        "Stop command",
        "End complete",
        "Purge complete",
        "Temp warning",
        "Pressure warning",
        "Check vacuum?",
        "Self-check fail",
        "Flow rate fail",
        "Temp control err",
        "Gas type err",
        "Temp reading err",
        "Suct temp err",
        "Sensor fail",
        "Brownout",
        "Sink overheat",
        "PSU overheat",
        "Power loss",
        "Refr too cold",
        "Refr time out",
        "Cryodrive connected?",
        "Cryodrive error",
        "No nitrogen",
        "No helium",
        "Vac gauge fail",
        "Vac reading error",
        "RS232 error",
        "Coldhead temp warning",
        "Coldhead temp error",
        "Wait for End",
        "Do not open",
        "Unplug Xtal sensor",
        "Cryostat open",
        "Cryostat open timeout",
        "High temp warning",
        "High temp error",
        "Cryodrive temp sensor fault",
        "Cryodrive pressure sensor fault",
        "Cryodrive low temp trip",
        "Cryodrive high temp trip",
        "Cryodrive low pressure trip",
        "Cryodrive high temp warning",
        "Cryodrive low pressure warning",
        "Gas supply connected?",
        "Autofill fault",
        "Autofill about to fill",
        "Autofill filling",
        "Collar temp err",
    ]

    def __init__(self, cryo):
        self.timestamp = time.time()
        self.gas_set_point = cryo.Set_temp
        self.gas_temp = cryo.Sample_temp
        self.gas_error = cryo.Temp_error
        self.run_mode_code = cryo.Run_mode
        self.run_mode = self.RUNMODE_CODES[self.run_mode_code]
        self.phase_code = cryo.Phase_id
        self.phase = self.PHASE_CODES[self.phase_code]
        self.ramp_rate = cryo.Ramp_rate
        self.target_temp = cryo.Target_temp
        self.evap_temp = cryo.Evap_temp
        self.suct_temp = cryo.Suct_temp
        self.remaining = cryo.Phase_time_remaining
        self.gas_flow = cryo.Gas_flow
        self.gas_heat = cryo.Gas_heat
        self.evap_heat = cryo.Evap_heat
        self.suct_heat = cryo.Average_suct_heat
        self.line_pressure = cryo.Back_pressure
        self.alarm_code = cryo.Alarm_code
        self.alarm = self.ALARM_CODES[self.alarm_code]
        self.run_time = cryo.Run_time
        self.run_days = self.run_time / (60 * 24)
        self.run_hours = (self.run_time - (self.run_days * 24 * 60)) / 60
        self.run_mins = (
            self.run_time - (self.run_days * 24 * 60) - (self.run_hours * 60)
        )


class Handler:
    """Control the cryostream class"""

    def __init__(self, cryoname):
        self._cryoname = cryoname
        self._loop = liboxford800.loop()
        if not liboxford800._OXFORD800:
            sleep(2)

    @get_cryo
    def restart(self, cryo):
        """Restart a Cryostream which has shut down"""
        cryo.restart()

    @get_cryo
    def purge(self, cryo):
        """Warm up the Coldhead as quickly as possible"""
        cryo.purge()

    @get_cryo
    def stop(self, cryo):
        """Immediately halt the Cryostream Cooler,turning off the pump and
        all the heaters - used for emergency only
        """
        cryo.stop()

    @get_cryo
    def hold(self, cryo):
        """Maintain temperature fixed indefinitely, until start issued."""
        cryo.hold()

    @get_cryo
    def pause(self, cryo):
        """Start temporary hold"""
        cryo.pause()

    @get_cryo
    def resume(self, cryo):
        """Exit temporary hold"""
        cryo.resume()

    @get_cryo
    def turbo(self, cryo, on_off):
        """Switch on/off the turbo gas flow
        Args:
          on_off (bool): True when turbo is on (gas flow 10 l/min)
        """
        cryo.turbo(on_off)

    @get_cryo
    def cool(self, cryo, temp):
        """Make gas temperature decrease to a set value as quickly as possible
        Args:
           temp (float): final temperature [K]
        """
        cryo.cool(temp)

    @get_cryo
    def plat(self, cryo, duration):
        """Maintain temperature fixed for a certain time.
        Args:
          duration (int): time [minutes] in range [1, 1440]
        """
        if duration < 1 or duration > 1440:
            raise ValueError("duration must be in range [1, 1440] [minutes]")

        cryo.plat(duration)

    @get_cryo
    def end(self, cryo, rate):
        """System shutdown with a ramprate to go back to temperature of 300K
        Args:
          rate (int): ramp rate [K/hour] in range [1, 360]
        """
        if rate < 1 or rate > 360:
            raise ValueError("ramprate must be in range [1, 360] [K/hour]")

        cryo.end(rate)

    @get_cryo
    def ramp(self, cryo, rate, temp):
        """Change gas temperature to a set value at a controlled rate
        Args:
           rate (int): ramp rate [K/hour] in range [1, 360]
           temp (float): target temperature [K]
        """
        if rate < 1 or rate > 360:
            raise ValueError("ramprate must be in range [1, 360] [K/hour]")

        return cryo.ramp(rate, temp)

    @get_cryo
    def is_ramping(self, cryo):
        """Read the ramping state.
        Returns:
            (bool): True if ramping. false otherwise.
        """
        cryo.wait_new_status()
        return cryo.Phase_id[1] in ["Ramp", "Wait", "Cool"]

    @get_cryo
    def read_gas_setpoint(self, cryo):
        """Read gas setpoint.
        Returns:
            (float): setpoint temperature [K].
        """
        cryo.wait_new_status()
        return cryo.Set_temp

    @get_cryo
    def read_gas_temperature(self, cryo):
        """Read gas temperature.
        Returns:
            (float): temperature [K].
        """
        cryo.wait_new_status()
        return cryo.Sample_temp

    @get_cryo
    def read_gas_error(self, cryo):
        """Read gas error.
        Return a value in Kelvin.
        """
        cryo.wait_new_status()
        return cryo.Temp_error

    @get_cryo
    def read_run_mode(self, cryo):
        """Read the current run mode (str)"""
        cryo.wait_new_status()
        return cryo.Run_mode[1]

    @get_cryo
    def read_phase(self, cryo):
        """Read the current phase (str)"""
        cryo.wait_new_status()
        return cryo.Phase_id[1]

    @get_cryo
    def read_ramprate(self, cryo):
        """Read the ramprate of current phase.
        Return a value in Kelvin/hour.
        """
        cryo.wait_new_status()
        return cryo.Ramp_rate

    @get_cryo
    def read_target_temperature(self, cryo):
        """Read the target temperature of the current phase.
        Return a value in Kelvin.
        """
        cryo.wait_new_status()
        return cryo.Target_temp

    @get_cryo
    def read_evap_temperature(self, cryo):
        """Read the evap temperature
        Return a value in Kelvin.
        """
        cryo.wait_new_status()
        return cryo.Evap_temp

    @get_cryo
    def read_suct_temperature(self, cryo):
        """Read the suct temperature
        Return a value in Kelvin.
        """
        cryo.wait_new_status()
        return cryo.Suct_temp

    @get_cryo
    def read_remaining_duration(self, cryo):
        """Read the remaining time of current phase (see cmd 'plat')"""
        cryo.wait_new_status()
        return cryo.Phase_time_remaining

    @get_cryo
    def read_gas_flow(self, cryo):
        """Read the gas flow (l/min)."""
        cryo.wait_new_status()
        return cryo.Gas_flow

    @get_cryo
    def read_gas_heat(self, cryo):
        """Read the gas heater."""
        cryo.wait_new_status()
        return cryo.Gas_heat

    @get_cryo
    def read_evap_heat(self, cryo):
        """Read the evap heater."""
        cryo.wait_new_status()
        return cryo.Evap_heat

    @get_cryo
    def read_suct_heat(self, cryo):
        """Read the suct heater."""
        cryo.wait_new_status()
        return cryo.Average_suct_heat

    @get_cryo
    def read_line_pressure(self, cryo):
        """Read Back pressure [100*bar]"""
        cryo.wait_new_status()
        return cryo.Back_pressure

    @get_cryo
    def read_alarm(self, cryo):
        """Read the alarm. Indicates most serious alarm condition"""
        cryo.wait_new_status()
        return cryo.Alarm_code

    @get_cryo
    def state_output(self, cryo):
        """Read the state.
        Returns:
            (tuple): Runnung, Phase id.
        """
        cryo.wait_new_status()
        return (cryo.Run_mode, cryo.Phase_id)

    @get_cryo
    def status(self, cryo):
        """Reat the full status.
        Returns:
            (str): Full status
        """
        return cryo.info()

    @property
    def statusPacket(self):
        cryo = liboxford800.get_handle(self._cryoname)
        if cryo is None:
            raise RuntimeError("Could not find oxford cryostream %r" % self._cryoname)
        return StatusPacket(cryo)


class Oxford800(Oxford700):
    """
    The only configuration parameter you need to fill
    if you have several cryostream 800 on the local network is *cryoname*.
    *cryoname* could be the name of the cryostream on the network or is ip address.
    """

    def __init__(self, config):
        super().__init__(config)

    def __info__(self):
        return self.hw_controller.status()

    # ------ init methods ------------------------

    @property
    def hw_controller(self):
        if self._hw_controller is None:
            liboxford800._get_oxford_form_address(self.config["cryoname"])
            self._hw_controller = Handler(self.config["cryoname"])
            global_map.register(self, children_list=[self._hw_controller])
        return self._hw_controller
