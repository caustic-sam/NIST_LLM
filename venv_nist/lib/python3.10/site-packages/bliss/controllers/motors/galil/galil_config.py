# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


"""
Configuration values for Galil DMC Controllers.
"""


# Controller model
# Model_id -> Serie dict.
# * MLL CMCS : DMC4010
# * MLL  : DMC2103 ?
# * ID21 : DMC30010  (packaged by Piezomotor)
MODEL_DICT = {
    "DMC50000": "MODEL_50000",
    "DMC52000": "MODEL_50000",
    "DMC4000": "MODEL_4000",
    "DMC4010": "MODEL_4000",
    "DMC4103": "MODEL_4000",
    "DMC4200": "MODEL_4000",
    "DMC30010": "MODEL_30000",
    "DMC2103": "MODEL_2000",
    "DMC2105": "MODEL_2000",
    "DMC1802": "MODEL_1800",
    "DMC1806": "MODEL_1800",
    "RIO47000": "MODEL_RIO47000",
    "EDD37010": "MODEL_EDD37010",
}

# Motor type
SERVO = 1
INV_SERVO = -1
STEPPER_LOW = 2
INV_STEPPER_LOW = 2.5
STEPPER_HIGH = -2
INV_STEPPER_HIGH = -2.5
MOTOR_TYPE_DICT = {
    "SERVO": SERVO,
    "INV_SERVO": INV_SERVO,
    "STEPPER_LOW": STEPPER_LOW,
    "INV_STEPPER_LOW": INV_STEPPER_LOW,
    "STEPPER_HIGH": STEPPER_HIGH,
    "INV_STEPPER_HIGH": INV_STEPPER_HIGH,
}

# Encoder type
QUADRA = 0
PULSE = 1
REVERSED_QUADRA = 2
REVERSED_PULSE = 3
ENCODER_TYPE_DICT = {
    "QUADRA": QUADRA,
    "PULSE": PULSE,
    "REVERSED_QUADRA": REVERSED_QUADRA,
    "REVERSED_PULSE": REVERSED_PULSE,
}
