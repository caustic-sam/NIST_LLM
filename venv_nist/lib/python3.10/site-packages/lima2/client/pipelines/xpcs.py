# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""XPCS pipeline subclass."""

import logging
from uuid import UUID

import numpy as np
import tango

from lima2.client import progress_counter, utils
from lima2.client.devencoded import structured_array
from lima2.client.pipeline import FrameSource, FrameType, Pipeline
from lima2.client.topology import TopologyKind

# Create a logger
logger = logging.getLogger(__name__)


class Xpcs(Pipeline):
    tango_class = "LimaProcessingXpcs"

    FRAME_SOURCES = {
        "input_frame": FrameSource(
            getter_name="getInputFrame",
            frame_type=FrameType.DENSE,
            saving_channel=None,
        ),
        "frame": FrameSource(
            getter_name="getFrame",
            frame_type=FrameType.DENSE,
            saving_channel="saving_dense",
        ),
        "sparse_frame": FrameSource(
            getter_name="getSparseFrame",
            frame_type=FrameType.SPARSE,
            saving_channel="saving_sparse",
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
    def channels(self) -> dict:
        """Returns the channels frame info"""
        # Lets assume same processing on each receivers
        return {
            "input_frame": self.input_frame_info[0],
            "frame": self.processed_frame_info[0],
            "sparse_frame": self.processed_frame_info[0],
        }

    @property
    def nb_fill_factors(self) -> progress_counter.ProgressCounter:
        """Get the number of fill factors fetchable by `pop_fill_factors()`."""
        return progress_counter.aggregate(
            single_counters=[
                progress_counter.SingleCounter(
                    name="nb_fill_factors",
                    value=dev.nb_fill_factors,
                    source=dev.name(),
                )
                for dev in self._devs
            ]
        )

    def pop_fill_factors(self) -> tuple[np.ndarray, np.ndarray]:
        """Pop fill factors from the server, return them ordered by frame index.

        Returns:
            A tuple (frame_indices, fill_factors) for each frame available from the server
            at the time of calling, where:
            - frame_indices is a ndarray of frame indices for each fill_factor
            - fill_factors is a ndarray (of the same size) of the fill factor of each frame
        """
        dtype = [
            ("frame_idx", "i4"),
            ("recv_idx", "i4"),
            ("fill_factor", "i4"),
        ]

        # Use cached data from previous call
        cache = self.get_cached_byproduct(
            "fill_factors", default=np.array([], dtype=dtype)
        )

        # Pop factors for new frames from each receiver and concatenate, mixing in cached data.
        # Data comes from server as 1D array with size (num_frames)
        new_data = np.concatenate(
            [cache.flatten()]
            + [
                structured_array.decode(dev.popFillFactors(), dtype)
                for dev in self._devs
            ]
        )

        logger.debug(f"Received fill factors for {new_data.shape[0]} frames")

        frame_indices = new_data["frame_idx"]
        frame_order = np.argsort(frame_indices)
        first_gap = utils.find_first_gap(frame_indices[frame_order])

        # Sort data by frame order
        data = new_data[frame_order]

        # Cache any data after the first frame gap for later
        self.cache_byproduct("fill_factors", data[first_gap:])

        # Now our data is contiguous
        data = data[:first_gap]

        logger.debug(f"Returning fill factors for frames: {data['frame_idx']}")

        return (data["frame_idx"], data["fill_factor"])
