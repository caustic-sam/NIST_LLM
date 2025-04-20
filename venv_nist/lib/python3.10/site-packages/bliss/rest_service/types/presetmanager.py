from __future__ import annotations
import logging
from typing import Literal, Optional

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
)

logger = logging.getLogger(__name__)

PresetmanagerStates = ["READY", "ERROR"]


class PresetmanagerPropertiesSchema(HardwareSchema):
    state: Optional[Literal[tuple(PresetmanagerStates)]] = Field(None, read_only=True)
    presets: Optional[list[str]] = None


class PresetmanagerCallablesSchema(CallableSchema):
    apply: Callable1Arg[str] = Field(description="The preset to apply")


class PresetmanagerType(ObjectType):
    NAME = "presetmanager"
    STATE_OK = [PresetmanagerStates[0]]

    PROPERTIES = PresetmanagerPropertiesSchema
    CALLABLES = PresetmanagerCallablesSchema


Default = PresetmanagerType
