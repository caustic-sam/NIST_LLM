# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.controllers.motor import CalcController
from bliss.common.logtools import log_debug
from bliss.common.protocols import HasMetadataForScan
from bliss.shell.standard import umvr

"""

Stable slits have the second (move) motor held by the moving part of the first
(support) motor. Hence, moving the offset consist in moving only the first
(support) motor.

This improves the stability of the slits: less vibrations and a constant gap.


  Support
   ___
  | 1 |_______________________________
  |___|     |                         |
            |                |        |
           _|_               |        |
          | 2 |______________|
          |___|
           Move
                         ___
                        | 1 | Support
                        |___|
                          |
              ___         |
        Move | 2 |________|
             |___|        |
               |          |
               |          |
               |    ______|
               |
               |
               |
               |_______

- "support": real motor holding the second motor
- "move": real motor supported by the motor "support"
- "support" motor MUST move positive when moving away from the motor block
- "move" motor MUST move positive when moving to the motor block
- "offset" calc motor is moving positive from (r)ing/(b)ottom to (h)all/(t)op
- "support_sign" yml parameter is 1.0 when "support" is moving as "offset", -1.0 if opposite
- when moving "offset", only "support" is moving

Horizontal slits
    support     : ring
    sign ring   : positive to RING
    move        : hall
    sign hall   : positive to HALL
    offset      : positive to HALL
Vertical slits
    support     : top
    sign top    : positive TOP
    move        : bottom
    sign bottom : positive BOTTOM
    offset      : positive to TOP

  controller:
    class: stable_slits
    type: [vertical/horizontal]
    axes:
        - name: $rup
          tags: real support
        - name: $rdown
          tags: real move
        - name: vgap
          tags: gap
        - name: voff
          tags: offset
"""


class StableSlits(CalcController, HasMetadataForScan):
    def __init__(self, *args, **kwargs):
        CalcController.__init__(self, *args, **kwargs)
        slit_type = self.config.get("type", None)
        if slit_type is None:
            raise RuntimeError(
                "stable_slits: Type [vertical/horizontal] not defined in YML file"
            )
        if slit_type.lower() not in ["vertical", "horizontal"]:
            raise RuntimeError("stable_slits: Type must be [vertical/horizontal]")
        if slit_type.lower() == "vertical":
            self.support_sign = 1.0
            self.blade_sign = 1.0
        else:
            self.support_sign = -1.0
            self.blade_sign = -1.0

    def scan_metadata(self):
        """this is metadata publishing to the Nexus file"""
        cur_pos = self._calc_from_real()
        meta_dict = {
            "gap": cur_pos["gap"],
            "offset": cur_pos["offset"],
            "@NX_class": "NXslit",
        }
        return meta_dict

    def align(self):
        """
        Put gap to 0 and offset at 0 at the middle of the motors range.
        """
        mot_support = self._tagged["support"][0]
        mot_move = self._tagged["move"][0]
        mot_support.hw_limit(mot_support.sign)
        mot_move.hw_limit(mot_move.sign)
        pos = mot_move.position
        mot_move.hw_limit(-mot_move.sign)
        stroke = abs(pos - mot_move.position)
        umvr(mot_support, -(stroke / 2.0))
        mot_support.dial = 0.0
        mot_support.offset = 0.0
        mot_move.dial = 0.0
        mot_move.offset = 0.0

    def calc_from_real(self, positions_dict):
        log_debug(self, "[STABLE SLITS] calc_from_real()")
        log_debug(self, "[STABLE SLITS]\treal: %s" % positions_dict)

        support = positions_dict["support"]
        move = positions_dict["move"]

        calc_dict = dict()
        calc_dict["offset"] = self.support_sign * support - self.blade_sign * 0.5 * move
        calc_dict["gap"] = move

        log_debug(self, "[STABLE SLITS]\tcalc: %s" % calc_dict)

        return calc_dict

    def calc_to_real(self, positions_dict):
        log_debug(self, "[STABLE SLITS] calc_to_real()")
        log_debug(self, "[STABLE SLITS]\tcalc: %s" % positions_dict)

        offset = positions_dict["offset"]
        gap = positions_dict["gap"]

        real_dict = dict()
        real_dict["support"] = self.support_sign * (
            offset + self.blade_sign * 0.5 * gap
        )
        real_dict["move"] = gap

        log_debug(self, "[STABLE SLITS]\treal: %s" % real_dict)

        return real_dict
