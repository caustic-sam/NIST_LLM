from __future__ import annotations
import logging
from typing import Literal, Optional
from pydantic import BaseModel

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
)

logger = logging.getLogger(__name__)

LimaStates = ["READY", "ACQUIRING", "CONFIGURATION", "FAULT", "UNKNOWN", "OFFLINE"]


class _LimaStaticPropertiesSchema(BaseModel):
    """Static information valid during the whole device life cycle.

    Properties from https://lima1.readthedocs.io/en/latest/applications/tango/python/doc/index.html
    """

    lima_version: str
    """The lima core library version number"""

    lima_type: str
    """Name of the camera plugin: Maxipix, Pilatus, Frelon, Pco, Basler, Simulator..."""

    camera_type: str
    """Type of the camera as exposed by the camera plugin."""

    camera_model: str
    """Model of the camera as exposed by the camera plugin: 5x1- TPX1"""

    camera_pixelsize: list[float] = Field(length=2)
    """The camera pixel size in x and y dimension, in micron.

    Despit the Lima Tango API, this value is returned in micron instead of meter.
    """

    image_max_dim: list[int] = Field(length=2)
    """Maximum image dimension, width and height in pixel"""


class LimaPropertiesSchema(HardwareSchema):
    state: Literal[tuple(LimaStates)] = Field("UNKNOWN", read_only=True)
    static: _LimaStaticPropertiesSchema | None = Field(read_only=True)
    rotation: Optional[int] = None
    binning: Optional[list[int]] = None
    raw_roi: Optional[list[int]] = Field(None, read_only=True)
    roi: Optional[list[int]] = Field(None, read_only=True)
    flip: Optional[list[bool]] = Field(None, read_only=True)
    size: Optional[list[int]] = Field(None, read_only=True)
    acc_max_expo_time: Optional[float] = None


class LimaCallablesSchema(CallableSchema):
    pass


class LimaType(ObjectType):
    NAME = "lima"
    STATE_OK = [LimaStates[0], LimaStates[1]]

    PROPERTIES = LimaPropertiesSchema
    CALLABLES = LimaCallablesSchema


Default = LimaType
