# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Lima2.Client package

.. autosummary::

    detector.Detector
    state_machine.State
"""

from lima2.client.client import Client
from lima2.client.detector import CommError
from lima2.client.state_machine import State
from lima2.client.pipeline import Pipeline

__all__ = ["Client", "CommError", "State", "Pipeline"]
