# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from random import randint, random
import time
import enum

from tango.server import run
from tango.server import Device
from tango.server import attribute, command
from tango import DevState


DevStates = (
    DevState.ALARM,
    DevState.CLOSE,
    DevState.CLOSE,
    DevState.FAULT,
    DevState.EXTRACT,
    DevState.MOVING,
    DevState.ON,
    DevState.OFF,
    DevState.OPEN,
    DevState.INSERT,
    DevState.INIT,
    DevState.STANDBY,
    DevState.UNKNOWN,
)


def randstate():
    """Returns a random tango state"""
    return DevStates[randint(0, len(DevStates) - 1)]


class SRMode(enum.Enum):
    Unknown = -1
    USM = 1
    MDT = 2
    Shutdown = 3
    SafetyTest = 4
    IdTest = 5
    RANDOM = 999


class SimulationMachInfo(Device):
    """
    Simulate a machinfo device.

    The refill is processed every `refill_period` time. With a duration of `refill_period`.

    The current is a random value.
    """

    Auto_Mode_Time = attribute(fget=lambda _: randint(0, 3), dtype=int)
    Auto_Mode_Time_Str = attribute(
        fget=lambda _: time.asctime(time.localtime()), dtype=str
    )
    Automatic_Mode = attribute(fget=lambda _: randint(0, 1), dtype=bool)
    Close_Delivery_Time = attribute(fget=lambda _: randint(0, 1000), dtype=int)
    EXP_Itlk_State = attribute(fget=randstate)
    FE_Itlk_State = attribute(fget=randstate)

    FE_State = attribute(fget=lambda _: "", dtype=str)
    Gap_Opened = attribute(fget=lambda _: randint(0, 1), dtype=bool)
    HQPS_Itlk_State = attribute(fget=randstate)
    Mode = attribute(fget=lambda _: randint(0, 3), dtype=int)
    Open_Close_counter = attribute(fget=lambda _: randint(100, 1000), dtype=int)
    PSS_Itlk_State = attribute(fget=randstate)
    SR_Current = attribute(fget="read_SR_Current", dtype=float)
    SR_Filling_Mode = attribute(fget=lambda _: "7/8 multibunch", dtype=str)
    SR_Lifetime = attribute(fget=lambda _: randint(10000, 70000), dtype=float)
    SR_Mode = attribute(fget="read_SR_Mode", dtype=int)
    SR_Operator_Mesg = attribute(
        fget=lambda _: "You are in Simulated Machine", dtype=str
    )
    SR_Refill_Countdown = attribute(fget="read_SR_Refill_Countdown", dtype=float)
    SR_Single_Bunch_Current = attribute(fget=lambda _: random() * 2, dtype=float)
    UHV_Valve2_State = attribute(fget=randstate)
    UHV_Valve_State = attribute(fget=randstate)

    def __init__(self, *args, **kwargs):
        Device.__init__(self, *args, **kwargs)
        self.set_state(DevState.ON)
        self.simulate_error = False
        self.delay = 0
        self.refill_period: float = 200
        self.refill_duration: float = 1
        self.sr_mode: SRMode = SRMode.RANDOM

    def always_executed_hook(self):
        time.sleep(self.delay)

    @command(dtype_in=bool)
    def setSimulateError(self, sim_error):
        self.simulate_error = sim_error

    @command(dtype_in=float)
    def setExtraDelay(self, delay):
        self.delay = delay

    @command(dtype_in=float)
    def setRefillPeriod(self, refill_period: float):
        """Setup the period of time of a refill (in second)"""
        self.refill_period = refill_period

    @command(dtype_in=str)
    def setSRMode(self, sr_mode: str):
        """Setup the SR mode"""
        self.sr_mode = SRMode[sr_mode]

    def read_SR_Mode(self):
        if self.sr_mode == SRMode.RANDOM:
            return randint(1, 3)
        return self.sr_mode.value

    def read_SR_Current(self):
        if self.simulate_error:
            raise RuntimeError("Simulated error")
        else:
            return randint(0, 200)

    def read_SR_Refill_Countdown(self):
        if self.simulate_error:
            raise RuntimeError("Simulated error")
        else:
            next_refill = self.refill_period - time.time() % self.refill_period
            return next_refill - self.refill_duration


if __name__ == "__main__":
    run((SimulationMachInfo,))
