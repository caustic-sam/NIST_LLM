# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

""" BaseShutter, BaseShutterState, ShutterSwitch, Shutter

Deprecated module, prefer to use `bliss.common.base_shutter` or
`bliss.controllers.shutters.shutter`.
"""

# Compatibility with BLISS <= 1.10
from .base_shutter import *  # noqa

# Compatibility with BLISS <= 1.10
from bliss.controllers.shutters.shutter import *  # noqa

from bliss.controllers.shutters.shutter import AxisWithExtTriggerShutter  # noqa

# Compatibility with BLISS <= 1.10
Shutter = AxisWithExtTriggerShutter
