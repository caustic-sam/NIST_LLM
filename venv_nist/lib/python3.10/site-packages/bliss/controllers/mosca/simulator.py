# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.controllers.mosca.base import McaController


class McaSimulator(McaController):
    STATS_MAPPING = {
        # MOSCA    :  NxWriter
        "livetime": "trigger_livetime",
        "realt_ms": "realtime",
        "elapsed_ms": "elapsed",
    }

    def _set_number_of_channels(self, channels_number):
        """Set the number of channels of the MCA simulation device"""
        self.hardware.setNumberModules(channels_number)
        self.initialize()

        # to force Mosca Simulator data buffer to take into account the new number of channels
        self.hardware.prepareAcq()
        self.hardware.startAcq()
        self.hardware.stopAcq()
