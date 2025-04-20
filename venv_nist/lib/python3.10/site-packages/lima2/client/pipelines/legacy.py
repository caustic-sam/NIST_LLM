# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Legacy pipeline subclass."""

import logging
from uuid import UUID

import tango
from beartype import beartype

from lima2.client.pipeline import FrameSource, FrameType, Pipeline
from lima2.client.topology import TopologyKind

# Create a logger
_logger = logging.getLogger(__name__)


@beartype
class Legacy(Pipeline):
    tango_class = "LimaProcessingLegacy"

    FRAME_SOURCES = {
        "input_frame": FrameSource(
            getter_name="getInputFrame",
            frame_type=FrameType.DENSE,
            saving_channel=None,
        ),
        "frame": FrameSource(
            getter_name="getFrame",
            frame_type=FrameType.DENSE,
            saving_channel="saving",
        ),
    }
    """Available frame sources."""

    def __init__(
        self,
        uuid: UUID,
        proc_devs: list[tango.DeviceProxy],
        topology_kind: TopologyKind,
        timeout: int,
    ):
        super().__init__(
            uuid=uuid, proc_devs=proc_devs, topology_kind=topology_kind, timeout=timeout
        )

    @property
    def channels(self):
        """Return the channel descriptions"""
        return {
            "input_frame": self.input_frame_info[0],
            "frame": self.processed_frame_info[0],
        }
