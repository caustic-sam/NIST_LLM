# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.common.logtools import log_debug

from .pi_e51x import PI_E51X

"""
Bliss controller for ethernet PI E517 piezo controller.
This controller exits in 2 version:
- PI E517.i1 that feats 1 channel : use class PI_E517_I1
- PI E517.i3 that feats 3 channels : use class PI_E517_I3 or PI_E517
This controller inherits all methods from PI_E51X.
Only the methods not common to E517 and E518 are redefined here:
   * gating.
   * configuration of trigger output (cto)
"""


class PI_E517(PI_E51X):
    def __init__(self, *args, **kwargs):
        PI_E51X.__init__(self, *args, **kwargs)
        self.model = "E517_i3"

    def set_gate(self, axis, state):
        """
        CTO  [<TrigOutID> <CTOPam> <Value>]+
         - <TrigOutID> : {1, 2, 3}
         - <CTOPam> :
             - 3: trigger mode
                      - <Value> : {0, 2, 3, 4}
                      - 0 : position distance
                      - 2 : OnTarget
                      - 3 : MinMaxThreshold   <----
                      - 4 : Wave Generator
             - 5: min threshold   <--- must be greater than low limit
             - 6: max threshold   <--- must be lower than high limit
             - 7: polarity : 0 / 1


        ex :      ID trigmod min/max       ID min       ID max       ID pol +
              CTO 1  3       3             1  5   0     1  6   100   1  7   1

        Args:
            - <state> : True / False
        Returns:
            -
        Raises:
            ?
        """
        _ch = axis.channel
        if state:
            _cmd = "CTO %d 3 3 %d 5 %g %d 6 %g %d 7 1" % (
                _ch,
                _ch,
                self._axis_low_limit[axis],
                _ch,
                self._axis_high_limit[axis],
                _ch,
            )
        else:
            _cmd = "CTO %d 3 3 %d 5 %g %d 6 %g %d 7 0" % (
                _ch,
                _ch,
                self._axis_low_limit[axis],
                _ch,
                self._axis_high_limit[axis],
                _ch,
            )

        log_debug(self, "set_gate :  _cmd = %s" % _cmd)
        self.command(_cmd)


class PI_E517_I1(PI_E517):
    def __init__(self, *args, **kwargs):
        PI_E517.__init__(self, *args, **kwargs)
        self.model = "E517_i1"
        self._nb_channel = 1

    def _get_cto(self, axis):
        _cto_ans = self.command("CTO?", nb_line=21)
        return _cto_ans


class PI_E517_I3(PI_E517):
    pass
