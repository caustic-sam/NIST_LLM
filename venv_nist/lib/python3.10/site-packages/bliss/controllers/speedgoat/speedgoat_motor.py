# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import tabulate
import enum


class MotorState(enum.IntEnum):
    Ready = 0
    Moving = 1
    LimitNeg = 2
    LimitPos = 3
    Stopped = 4
    Error = 5


"""
SPEEDGOAT MOTORS
"""


class SpeedgoatHdwMotorController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._motors = None
        self._load()

    def __info__(self):
        if self._motors is None:
            return "    No Motor in the model"
        lines = [["    ", "Name", "Unique Name"]]
        for motor in self._motors.values():
            lines.append(["    ", motor.name, motor._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._motors is None or force:
            motors = self._speedgoat._get_all_objects_from_key("bliss_motor")
            if len(motors) > 0:
                self._motors = {}
                for motor in motors:
                    sp_motor = SpeedgoatHdwMotor(self._speedgoat, motor)
                    setattr(self, sp_motor.name, sp_motor)
                    self._motors[sp_motor.name] = sp_motor
        return self._motors


class SpeedgoatHdwMotor:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(f"{unique_name}/bliss_motor/String")

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        lines.append(["", ""])
        lines.append(["State", self.state])
        lines.append(["Position", self.position])
        lines.append(["", ""])
        lines.append(["Velocity", self.velocity])
        lines.append(["Acc. Time", self.acc_time])
        lines.append(["Limit Neg.", self.limit_neg])
        lines.append(["Limit Pos.", self.limit_pos])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _tree(self):
        return self._speedgoat.parameter._load()["param_tree"].subtree(
            self._unique_name
        )

    def start(self):
        val = self._speedgoat.parameter.get(f"{self._unique_name}/start_trigger/Bias")
        self._speedgoat.parameter.set(
            f"{self._unique_name}/start_trigger/Bias", val + 1
        )

    def stop(self):
        val = self._speedgoat.parameter.get(f"{self._unique_name}/stop_trigger/Bias")
        self._speedgoat.parameter.set(f"{self._unique_name}/stop_trigger/Bias", val + 1)

    @property
    def acc_time(self):
        return float(self._speedgoat.parameter.get(f"{self._unique_name}/acc_time"))

    @acc_time.setter
    def acc_time(self, acc_time):
        self._speedgoat.parameter.set(f"{self._unique_name}/acc_time", acc_time)

    @property
    def limit_neg(self):
        return float(self._speedgoat.parameter.get(f"{self._unique_name}/limit_neg"))

    @limit_neg.setter
    def limit_neg(self, limit_neg):
        self._speedgoat.parameter.set(f"{self._unique_name}/limit_neg", limit_neg)

    @property
    def limit_pos(self):
        return float(self._speedgoat.parameter.get(f"{self._unique_name}/limit_pos"))

    @limit_pos.setter
    def limit_pos(self, limit_pos):
        self._speedgoat.parameter.set(f"{self._unique_name}/limit_pos", limit_pos)

    @property
    def setpoint(self):
        return float(self._speedgoat.parameter.get(f"{self._unique_name}/setpoint"))

    @setpoint.setter
    def setpoint(self, setpoint):
        self._speedgoat.parameter.set(f"{self._unique_name}/setpoint", setpoint)

    @property
    def velocity(self):
        return float(self._speedgoat.parameter.get(f"{self._unique_name}/velocity"))

    @velocity.setter
    def velocity(self, velocity):
        self._speedgoat.parameter.set(f"{self._unique_name}/velocity", velocity)

    @property
    def position(self):
        return float(self._speedgoat.signal.get(f"{self._unique_name}/motor_position"))

    @property
    def state(self):
        return MotorState(
            int(self._speedgoat.signal.get(f"{self._unique_name}/motor_state"))
        )
