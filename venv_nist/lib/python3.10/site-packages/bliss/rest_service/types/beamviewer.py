import logging
from typing import Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
    EmptyCallable,
)

logger = logging.getLogger(__name__)

BeamviewerStates = ["ON", "OFF", "UNKNOWN", "FAULT"]


class BeamviewerPropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(BeamviewerStates)]] = Field(None, read_only=True)
    foil: Optional[Literal["IN", "OUT", "UNKNOWN", "NONE"]] = Field(
        None, read_only=True
    )
    led: Optional[Literal["ON", "OFF"]] = Field(None, read_only=True)
    screen: Optional[Literal["IN", "OUT", "UNKNOWN"]] = Field(None, read_only=True)
    diode_ranges: Optional[str] = Field(None, read_only=True)
    diode_range: Optional[str] = None


class BeamviewerCallablesSchema(CallableSchema):
    led: Callable1Arg[bool]
    screen: Callable1Arg[bool]
    foil: Callable1Arg[bool]
    current: EmptyCallable


class BeamviewerType(ObjectType):
    NAME = "beamviewer"
    STATE_OK = [BeamviewerStates[0], BeamviewerStates[1]]

    PROPERTIES = BeamviewerPropertiesSchema
    CALLABLES = BeamviewerCallablesSchema


Default = BeamviewerType
