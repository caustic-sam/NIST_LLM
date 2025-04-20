# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Contains Mosca ROI geometries.

This have to have no dependecies in order to be imported from outside of BLISS.
"""

from __future__ import annotations
import dataclasses


@dataclasses.dataclass(frozen=True)
class McaRoi:
    name: str
    """Name of the ROI"""

    start: int
    """Begining of the ROI range (included)"""

    stop: int
    """End of the ROI range (included)"""

    channel: None | int | tuple[int, int]
    """MCA channel alias where ROI is applied.

    It can be one of:

    - None   : roi is applied to all channels (will produce one value per channel)
    - n (>=0): roi is applied to one channel  (will produce one value for channel alias 'n')
    - -1     : roi is applied to all channels (will produce one value as the sum of all channels)
    - (n, m) : roi is applied to channels in range [n,m] (will produce one value as the sum of all channels in that range)

    """

    def to_dict(self):
        """Return typical info as a dict"""
        return {
            "name": self.name,
            "start": self.start,
            "stop": self.stop,
            "channel": self.channel,
        }
