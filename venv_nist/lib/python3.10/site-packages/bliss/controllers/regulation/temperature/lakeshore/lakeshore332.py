# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Lakeshore 331 can communicate via a Serial line (RS232) or a GPIB interface
"""

import enum

from bliss.comm.util import get_comm
from bliss.controllers.regulation.temperature.lakeshore.lakeshore331 import LakeShore331


# --- patch the Input, Output and Loop classes
from bliss.controllers.regulation.temperature.lakeshore.lakeshore import (  # noqa: F401
    LakeshoreInput as Input,
)
from bliss.controllers.regulation.temperature.lakeshore.lakeshore import (  # noqa: F401
    LakeshoreOutput as Output,
)
from bliss.controllers.regulation.temperature.lakeshore.lakeshore import (  # noqa: F401
    LakeshoreLoop as Loop,
)


class LakeShore332(LakeShore331):
    @enum.unique
    class SensorTypes(enum.IntEnum):
        Silicon_Diode = 0
        GaAlAs_Diode = 1
        Platinium_250_100_ohm = 2
        Platinium_500_100_ohm = 3
        Platinium_1000_ohm = 4
        NTC_RTD_75_mV_7500_ohm = 5
        Thermocouple_25_mV = 6
        Thermocouple_50_mV = 7
        NTC_RTD_75_mV_75_ohm = 8
        NTC_RTD_75_mV_750_ohm = 9
        NTC_RTD_75_mV_7500_ohm_bis = 10
        NTC_RTD_75_mV_75000_ohm = 11
        NTC_RTD_75_mV_auto = 12

    def init_com(self):
        self._model_number = 332
        if "serial" in self.config:
            self._comm = get_comm(self.config, parity="O", bytesize=7, stopbits=1)
        else:
            self._comm = get_comm(self.config)
