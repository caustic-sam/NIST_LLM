# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Classes and functions to handle receiver topologies (single, round-robin, etc)."""

import copy
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum

import tango as tg
from beartype import beartype
from typing_extensions import Optional, override

from lima2.client import pipelines
from lima2.client.devencoded.dense_frame import EncodedFrame


@beartype
class TopologyKind(Enum):
    """Receiver topology types.

    The control device should expose a receiver_topology property with a value matching one
    of these options.
    """

    SINGLE = "single"
    ROUND_ROBIN = "round_robin"
    UNEVEN = "uneven"


@beartype
class Topology(ABC):
    """Receiver topology interface."""

    @abstractmethod
    def lookup(self, frame_idx: int, getters: list[Callable]) -> EncodedFrame:
        """Look up a frame by global index.

        Args:
            getters: List of bound methods to call to obtain a frame by index from a single
              receiver. One element per receiver. E.g. `getters[0](1)` attempts to get
              frame 1 from receiver 0.

        Returns the raw devencoded frame data to be decoded.
        """


@beartype
class RoundRobinTopology(Topology):
    """Multiple-receiver topology where the receiver ordering is fixed throughout the acquisition.

    This class represents a static, strict round robin where the ordering is fixed at prepare-time.
    """

    def __init__(self, num_receivers: int, ordering: list[int]):
        self.num_receivers = num_receivers
        """Number of receivers"""

        self.ordering = ordering
        """Ordering of receivers: list of indices specifying who gets a given frame.

        E.g. for two receivers, ordering = [1, 0] means:
        - receiver 1 gets the first frame
        - receiver 0 gets the second frame
        - receiver 1 gets the third frame
        and so on.
        `ordering[i % num_receivers]` yields the index of the receiver which acquired frame i.
        """

    def lookup(self, frame_idx: int, getters: list[Callable]) -> EncodedFrame:
        """Use the fixed receiver ordering to find the correct receiver."""
        rcv_idx = self.ordering[frame_idx % self.num_receivers]
        frame: EncodedFrame = getters[rcv_idx](frame_idx)
        return frame


@beartype
class SingleTopology(Topology):
    """Single receiver topology."""

    @override
    def lookup(self, frame_idx: int, getters: list[Callable]) -> EncodedFrame:
        frame: EncodedFrame = getters[0](frame_idx)
        return frame


@beartype
class UnevenTopology(Topology):
    """A multi-receiver topology where the acquisition order is unpredictable.

    For instance, when the detector has an internal mechanism for load-balancing across receivers,
    there is no simple way to map a frame index to a receiver. In such cases, frame lookup is done
    by trial and error.
    """

    def __init__(self, num_receivers: int):
        self.num_receivers = num_receivers
        """Number of receivers"""

    @override
    def lookup(self, frame_idx: int, getters: list[Callable]) -> EncodedFrame:
        """Try to find the frame on every processing device.

        If the frame is found on multiple devices (e.g. frame_idx=-1),
        return the one found last (not necessarily the most recent).
        """
        frame: Optional[EncodedFrame] = None

        for i in range(self.num_receivers):
            try:
                frame = getters[i](frame_idx)
            except tg.DevFailed:
                pass

        if frame is None:
            raise IndexError(f"Frame {frame_idx} not available from any receiver")

        return frame


def distribute_acq(
    ctl_params: dict, acq_params: dict, proc_params: dict, num_receivers: int
) -> tuple[dict, list[dict], list[dict]]:
    """Reinterpret params for a distributed acquisition.

    Given the number of receivers, return the appropriate set of acquisition and processing params
    for each receiver.

    Does not modify the input dictionaries.

    Returns:
        Tuple (ctl_params, list[acq_params], list[proc_params]) adjusted for
        homogeneous acquisition. These can be passed directly to `Detector.prepare_acq()`.

    Example:
        ```
        ctl, acq, proc = topology.distribute_acq(ctl, acq, proc, 16)
        detector.prepare_acq(ctl, acq, proc)
        ```
    """

    # Clone params
    ctl = copy.deepcopy(ctl_params)
    acq = [copy.deepcopy(acq_params) for _ in range(num_receivers)]
    proc = [copy.deepcopy(proc_params) for _ in range(num_receivers)]

    for i in range(num_receivers):
        # Assign unique filename rank per receiver
        processing_class = pipelines.get_class(proc_params["class_name"])
        for source in processing_class.FRAME_SOURCES.values():
            if source.saving_channel is not None:
                proc[i][source.saving_channel]["filename_rank"] = i

        # Idem for detector RAW saving params
        if "saving" in acq[i]:
            acq[i]["saving"]["filename_rank"] = i

    return ctl, acq, proc
