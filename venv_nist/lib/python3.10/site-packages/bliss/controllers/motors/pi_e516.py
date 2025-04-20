# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss.common.utils import object_method
from bliss.common.logtools import log_debug

from .pi_e51x import PI_E51X

"""
Bliss controller for gpib/rs232 PI E516 piezo controller.
"""


class PI_E516(PI_E51X):
    def __init__(self, *args, **kwargs):
        PI_E51X.__init__(self, *args, **kwargs)
        self.model = "E516"

    def _set_velocity_control_mode(self, axis):
        """
        The velocity control mode for the axis should be set to ON (with VCO
        command, p. 78) to have the control voltage increasing continuous during a
        certain (small) time period. Otherwise the control voltage would
        immediately reach the target value, and ONT? monitoring would make no
        sense.
        """
        self.command(f"VCO {axis.chan_letter}1")

    def set_on(self, axis):
        """
        Set ALL axes Online. Not channel-specific.
        """
        log_debug(self, "set %s ONLINE" % axis.name)
        self.command("ONL 1")
        self._axis_online[axis] = 1

    def set_off(self, axis):
        log_debug(self, "set %s OFFLINE" % axis.name)
        self.command("ONL 0")
        self._axis_online[axis] = 0

    def set_velocity(self, axis, new_velocity):
        self.command(f"VEL {axis.chan_letter}{new_velocity}")
        log_debug(self, "%s velocity set : %g" % (axis.name, new_velocity))
        return self.read_velocity(axis)

    def start_one(self, motion):
        """
        - Send 'MOV' or 'SVA' depending on closed loop mode.

        Args:
            - <motion> : Bliss motion object.

        Return:
            - None
        """
        chan_letter = motion.axis.chan_letter
        tg_pos = motion.target_pos

        if self._axis_closed_loop[motion.axis]:
            # Command in position.
            log_debug(self, "Move %s in position to %g" % (motion.axis.name, tg_pos))
            self.command(f"MOV {chan_letter}{tg_pos}")
        else:
            # Command in voltage.
            log_debug(self, f"Move {motion.axis.name} in voltage to {tg_pos}")
            self.command(motion.axis, f"SVA {chan_letter}{tg_pos}")

    def stop(self, axis):
        """
        * HLT -> stop smoothly
        * STP -> stop asap
        * 24    -> stop asap
        * to check : copy of current position into target position ???
        """
        self.command("STP %s" % axis.chan_letter)

    """
    E516 specific
    """

    def _get_pos(self):
        """
        Args:
            - <axis> :
        Return:
            - <position>: real positions (POS? command) read by capacitive sensor.
        """

        # TODO: use "POS? A B C" ?

        _ans = []
        _ans.append(self.command("POS? A"))
        _ans.append(self.command("POS? B"))
        _ans.append(self.command("POS? C"))
        _pos = list(map(float, _ans))

        return _pos

    def _get_target_pos(self, axis):
        """Return last targets positions for all 3 axes.
            - (MOV?/SVA? command) (setpoint value).
            - SVA? : Query the commanded output voltage (voltage setpoint).
            - MOV? : Return the last valid commanded target position.
        Args:
            - <>
        Return:
            - list of float
        """
        _ans = []
        if self._axis_closed_loop[axis]:
            _ans.append(self.command("MOV? A"))
            _ans.append(self.command("MOV? B"))
            _ans.append(self.command("MOV? C"))
        else:
            _ans.append(self.command("SVA? A"))
            _ans.append(self.command("SVA? B"))
            _ans.append(self.command("SVA? C"))
        _pos = list(map(float, _ans))

        return _pos

    """
    DCO : Drift Compensation Offset.
    """

    def _set_dco(self, axis, onoff):
        self.command(f"DCO {axis.chan_letter}{onoff}")

    """
    Voltage commands
    """

    @object_method(types_info=("None", "float"))
    def get_output_voltage(self, axis):
        """
        Return output voltage
        """
        return float(self.command(f"VOL? {axis.channel}"))
