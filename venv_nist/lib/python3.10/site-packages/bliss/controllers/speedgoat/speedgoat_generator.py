# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import tabulate
import enum

"""
SPEEDGOAT Signal Generators
"""


class SpeedgoatHdwGeneratorController:
    def __init__(self, speedgoat):
        self._speedgoat = speedgoat
        self._generators = None
        self._load()

    def __info__(self):
        if self._generators is None:
            return "    No Generator in the model"
        lines = [["    ", "Name", "Unique Name"]]
        for generator in self._generators.values():
            lines.append(["    ", generator.name, generator._unique_name])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _load(self, force=False):
        if self._generators is None or force:
            generators = self._speedgoat._get_all_objects_from_key("bliss_signalgen")
            if len(generators) > 0:
                self._generators = {}
                for generator in generators:
                    sp_generator = SpeedgoatHdwGenerator(self._speedgoat, generator)
                    setattr(self, sp_generator.name, sp_generator)
                    self._generators[sp_generator.name] = sp_generator
        return self._generators


class GeneratorState(enum.IntEnum):
    Idle = 0
    Moving = 1
    Stopped = 2
    Fault = 3


class GeneratorType(enum.IntEnum):
    Step = 1
    Impulse = 2
    Ramp = 3
    Sinus = 4
    LinearSweep = 5
    LogSweep = 6
    Triangle = 7
    UniformWhiteNoise = 8
    NormalWhiteNoise = 9


class SpeedgoatHdwGenerator:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name
        self.name = self._speedgoat.parameter.get(
            f"{unique_name}/bliss_signalgen/String"
        )
        self.type = GeneratorTypeClass(self._speedgoat, self._unique_name)

        self._type_param = {
            "Step": ["amplitude", "duration", "offset"],
            "Impulse": ["amplitude", "offset"],
            "Ramp": ["amplitude", "duration", "offset"],
            "Sinus": ["amplitude", "duration", "offset", "frequency"],
            "LinearSweep": [
                "amplitude",
                "duration",
                "offset",
                "start_freq",
                "end_freq",
            ],
            "LogSweep": ["amplitude", "duration", "offset", "start_freq", "end_freq"],
            "Triangle": ["amplitude", "duration", "offset", "frequency"],
            "UniformWhiteNoise": ["amplitude", "duration", "offset"],
            "NormalWhiteNoise": ["amplitude", "duration", "offset"],
        }

    def __info__(self):
        lines = []
        lines.append(["Name", self.name])
        lines.append(["Unique Name", self._unique_name])
        lines.append(["", ""])
        type_str = self.type._get_type_str()
        lines.append(["Type", type_str])
        lines.append(["", ""])
        for param in self._type_param[type_str]:
            lines.append([param, getattr(self, param)])
        mystr = "\n" + tabulate.tabulate(lines, tablefmt="plain", stralign="right")
        return mystr

    def _tree(self):
        self._speedgoat.parameter._load()["param_tree"].subtree(
            self._unique_name
        ).show()

    @property
    def amplitude(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/amplitude")

    @amplitude.setter
    def amplitude(self, value):
        self._speedgoat.parameter.set(f"{self._unique_name}/amplitude", value)

    @property
    def duration(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/duration")

    @duration.setter
    def duration(self, value):
        self._speedgoat.parameter.set(f"{self._unique_name}/duration", value)

    @property
    def start_freq(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/start_freq")

    @start_freq.setter
    def start_freq(self, value):
        self._speedgoat.parameter.set(f"{self._unique_name}/start_freq", value)

    @property
    def end_freq(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/end_freq")

    @end_freq.setter
    def end_freq(self, value):
        self._speedgoat.parameter.set(f"{self._unique_name}/end_freq", value)

    @property
    def frequency(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/frequency")

    @frequency.setter
    def frequency(self, value):
        self._speedgoat.parameter.set(f"{self._unique_name}/frequency", value)

    @property
    def offset(self):
        return self._speedgoat.parameter.get(f"{self._unique_name}/offset")

    @offset.setter
    def offset(self, value):
        self._speedgoat.parameter.set(f"{self._unique_name}/offset", value)

    def start(self):
        start_trigger = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/start_trigger/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/start_trigger/Bias", start_trigger + 1
        )

    def stop(self):
        stop_trigger = int(
            self._speedgoat.parameter.get(f"{self._unique_name}/stop_trigger/Bias")
        )
        self._speedgoat.parameter.set(
            f"{self._unique_name}/stop_trigger/Bias", stop_trigger + 1
        )

    @property
    def state(self):
        return GeneratorState(
            int(self._speedgoat.signal.get(f"{self._unique_name}/state"))
        )

    @property
    def output(self):
        return self._speedgoat.signal.get(f"{self._unique_name}/gen_signal")


class GeneratorTypeClass:
    def __init__(self, speedgoat, unique_name):
        self._speedgoat = speedgoat
        self._unique_name = unique_name

    def __info__(self):
        return f"    {self._get_type_str()}"

    def _get_type_str(self):
        return self.get().name

    def get(self):
        return GeneratorType(
            int(self._speedgoat.parameter.get(f"{self._unique_name}/signal_type"))
        )

    @property
    def Step(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 1)

    @property
    def Impulse(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 2)

    @property
    def Ramp(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 3)

    @property
    def Sinus(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 4)

    @property
    def LinearSweep(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 5)

    @property
    def LogSweep(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 6)

    @property
    def Triangle(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 7)

    @property
    def UniformWhiteNoise(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 8)

    @property
    def NormalWhiteNoise(self):
        self._speedgoat.parameter.set(f"{self._unique_name}/signal_type", 9)
