from __future__ import annotations
import logging
from typing import Literal, Optional

import numpy as np
import cv2

from PIL import Image

from bliss.rest_service.typedef import (
    ObjectType,
    HardwareSchema,
    Field,
    CallableSchema,
    Callable1Arg,
)

logger = logging.getLogger(__name__)

CameraStates = ["READY", "ACQUIRING", "UNKNOWN", "ERROR"]


class CameraPropertiesSchema(HardwareSchema):
    state: Optional[Literal[CameraStates]] = Field(None, read_only=True)
    exposure: Optional[float] = None
    gain: Optional[float] = None
    mode: Optional[str] = Field(None, read_only=True)
    live: Optional[bool] = None
    width: Optional[int] = Field(None, read_only=True)
    height: Optional[int] = Field(None, read_only=True)


class CameraCallablesSchema(CallableSchema):
    save: Callable1Arg[str]


class CameraType(ObjectType):
    NAME = "camera"
    STATE_OK = [CameraStates[0], CameraStates[1]]

    PROPERTIES = CameraPropertiesSchema
    CALLABLES = CameraCallablesSchema

    def frame(self):
        """Return a frame from the camera"""
        return np.array([])

    def _call_save(self, path, type="PNG"):
        """Return a frame from the camera to disk"""
        frame = self.frame()
        if frame is None:
            raise RuntimeError("Could not get frame from camera")

        # Possible the frame will not be uint8, convert it
        if frame.dtype != np.uint8:
            # opencv does not support uint32?
            # https://github.com/MouseLand/cellpose/issues/937
            if frame.dtype == np.uint32:
                frame = frame.astype(np.float32)
            frame = cv2.normalize(frame, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
            frame = frame.astype(np.uint8)

        im = Image.fromarray(frame)
        if im.mode != "RGB":
            im = im.convert("RGB")

        im.save(path, type, quality=100)


Default = CameraType
