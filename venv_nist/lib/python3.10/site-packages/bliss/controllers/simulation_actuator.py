# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Class to simulate get and set method for testing actuators"""


class SimulationActuator:
    """Simulation class"""

    def __init__(self, name, config):
        self.name = name
        self._config = config
        self.__in = False
        self.__out = False

    def set(self, cmd, arg):
        """Simulated set method"""
        if cmd == "set_in":
            self.__in = bool(arg)
            self.__out = not self.__in
        if cmd == "set_out":
            self.__out = bool(arg)
            self.__in = not self.__out

    def get(self, cmd):
        """Simulated get method"""
        return self.__in if cmd == "set_in" else self.__out
