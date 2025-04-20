# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Lakeshore 336 can communicate via Ethernet, USB or a GPIB interface.
"""

from bliss.comm.util import get_comm
from bliss.common.logtools import log_debug

from bliss.controllers.regulation.temperature.lakeshore.lakeshore335 import LakeShore335

# --- patch the Input, Output and Loop classes
from bliss.controllers.regulation.temperature.lakeshore.lakeshore335 import (  # noqa: F401
    Input,
)
from bliss.controllers.regulation.temperature.lakeshore.lakeshore import (  # noqa: F401
    LakeshoreOutput as Output,
)
from bliss.controllers.regulation.temperature.lakeshore.lakeshore import (  # noqa: F401
    LakeshoreLoop as Loop,
)


class LakeShore336(LakeShore335):

    NUMINPUT = {1: "A", 2: "B", 3: "C", 4: "D"}
    REVINPUT = {"A": 1, "B": 2, "C": 3, "D": 4}

    VALID_INPUT_CHANNELS = ["A", "B", "C", "D"]
    VALID_OUTPUT_CHANNELS = [1, 2, 3, 4]
    VALID_LOOP_CHANNELS = [1, 2, 3, 4]

    def init_com(self):
        self._model_number = 336
        if "serial" in self.config:
            self._comm = get_comm(
                self.config, baudrate=57600, parity="O", bytesize=7, stopbits=1
            )
        else:
            self._comm = get_comm(self.config)

    def read_value_percent(self, touput):
        """Return ouptut current value as a percentage (%).

        Args:
            touput:  Output class type object
        """
        log_debug(self, "read_value_percent")
        if int(touput.channel) in [1, 2]:
            return self.send_cmd("HTR?", channel=touput.channel)
        elif int(touput.channel) in [3, 4]:
            return self.send_cmd("AOUT?", channel=touput.channel)
        else:
            raise ValueError(
                f"Wrong output channel: '{touput.channel}' should be in {self.VALID_OUTPUT_CHANNELS} "
            )
