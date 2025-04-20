from __future__ import annotations
from typing import Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
    EmptyCallable,
)

MotorStates = [
    "READY",
    "MOVING",
    "FAULT",
    "UNKNOWN",
    "DISABLED",
    "LOWLIMIT",
    "HIGHLIMIT",
    "HOME",
    "OFF",
]


class MotorPropertiesSchema(HardwareSchema):
    state: list[Literal[tuple(MotorStates)] | str] = Field(["UNKNOWN"], read_only=True)
    """List of actual state of the motor.

    Extra non-standard state can also be exposed with the following template:

    - `_MYSTATE:The state description`

    It have to be prefixed with an underscore, and can have a colon
    separator with a description.
    """

    position: float

    target: Optional[float]
    """Is None only when it not in `MOVING` state"""

    tolerance: float

    acceleration: Optional[float]
    """
    Acceleration in unit*s-2
    
    Can be `None` for a pseudo motor
    """

    velocity: Optional[float]
    """
    Velocity in unit/s

    Can be `None` for a pseudo motor
    """

    limits: list[float] = Field(length=2)
    """Limits for the position, such as limits[0] <= position <= limits[1]"""

    unit: Optional[str] = Field(read_only=True)
    """Unit of the motor position"""

    offset: float
    """Offset between the dial/user position."""

    sign: int
    """Sign between the dial/user position.

    Can be 1 or -1 only. Other values could be used as workaround.
    """

    display_digits: int = Field(read_only=True)
    """Number of digits to display the position."""


class MotorCallablesSchema(CallableSchema):
    move: Callable1Arg[float]
    rmove: Callable1Arg[float]
    stop: EmptyCallable
    wait: EmptyCallable


class MotorType(ObjectType):
    NAME = "motor"
    STATE_OK = [MotorStates[0], MotorStates[1]]

    PROPERTIES = MotorPropertiesSchema
    CALLABLES = MotorCallablesSchema


Default = MotorType
