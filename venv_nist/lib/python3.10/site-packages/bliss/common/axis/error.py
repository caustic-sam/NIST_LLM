# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


class AxisOnLimitError(RuntimeError):
    pass


class AxisOffError(RuntimeError):
    pass


class AxisFaultError(RuntimeError):
    pass
