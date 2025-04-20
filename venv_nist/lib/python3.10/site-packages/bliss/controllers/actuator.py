# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Actuator with two positions - IN and OUT. Requires that the underlying
controller has at least a method set and eventually a method get.

Example yml file:

.. code-block:: yaml

    name: detcover
    class: actuator
    controller: $wcd29g
    actuator_cmd: detcover
    actuator_state_in: detcover_in
    actuator_state_out: detcover_out
    actuator_inout: {"in": 0, "out": 1}
"""
from bliss.common.actuator import AbstractActuator


class Actuator(AbstractActuator):
    """Actuator with two positions class"""

    def __init__(self, name, config):
        self.name = name
        self.controller = config["controller"]
        self.key_in = config.get("actuator_state_in")
        self.key_out = config.get("actuator_state_out")
        self.key_cmd = config.get("actuator_cmd")
        self.inout = dict(config.get("actuator_inout", {"in": 1, "out": 0}))
        super().__init__()

    def _set_in(self):
        """Set the actuator in position IN."""
        self.controller.set(self.key_cmd, self.inout["in"])

    def _set_out(self):
        """Set the actuator in position OUT."""
        self.controller.set(self.key_cmd, self.inout["out"])

    def _is_in(self):
        """Check if the actuator is in position IN.

        Returns:
            (bool): True if IN. False if OUT, None if no check possible.
        """
        if self.key_in:
            return self.controller.get(self.key_in)
        if self.key_out:
            return not self.controller.get(self.key_out)
        return None

    def _is_out(self):
        """Check if the actuator is in position OUT.

        Returns:
            (bool): True if OUT. False if IN, None if no check possible.
        """
        if self.key_out:
            return self.controller.get(self.key_out)
        if self.key_in:
            return not self.controller.get(self.key_in)
        return None


class ActuatorMockup(AbstractActuator):
    """Actuator with two positions mock class"""

    def __init__(self, name, config):
        self.name = name
        self.inout = dict(config.get("actuator_inout", {"in": 1, "out": 0}))
        super().__init__()
        self.force_error = False
        self._in = None

    def _set_in(self):
        """Set the actuator IN"""
        self._in = True

    def _set_out(self):
        """Set the actuator OUT"""
        self._in = False

    @property
    def is_in(self):
        """Check if the actuator is in position IN.

        Returns:
            (bool): True if IN. False if OUT, None if no check possible.
        """
        return None if self.force_error else self._in

    @property
    def is_out(self):
        """Check if the actuator is in position OUT.

        Returns:
            (bool): True if OUT. False if IN, None if no check possible.
        """
        return None if self.force_error else self._in
