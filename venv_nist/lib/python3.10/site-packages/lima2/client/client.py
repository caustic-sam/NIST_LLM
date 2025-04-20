# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""User-facing interface exposing Lima2's control and acquisition API.

The role of the client is to connect the user to the Lima2 devices via Tango.

The Client object instantiates a `Detector` object, allowing the user to control the acquisition
via the standard prepare/start/stop sequence.

The client hides away the topology by providing aggregated stats and counters in the form of
`ProgressCounter` objects. If needed, these can be explored to trace the information back to
individual receivers, for instance if one of them is stuck on a frame while the rest are still
processing data.
"""

import logging
import os
import traceback
from concurrent import futures
from uuid import UUID

import tango as tg
import yaml
from beartype import beartype
from typing_extensions import Any, Optional

from lima2.client import pipelines, progress_counter, state_machine, topology
from lima2.client.detector import Detector
from lima2.client.pipeline import Pipeline
from lima2.client.progress_counter import ProgressCounter, SingleCounter
from lima2.client.topology import TopologyKind
from lima2.client.utils import docstring_from

logger = logging.getLogger(__name__)


@beartype
class Client:
    """Lima2 user-facing client."""

    def __init__(
        self,
        ctl_dev: tg.DeviceProxy,
        rcv_devs: list[tg.DeviceProxy],
        tango_timeout_s: int = 10,
    ):
        """Construct a Client given tango device proxies.

        The Client can also be built from a yaml config file (see `from_yaml`).
        The topology is queried from the control device properties.
        """

        self._detector = Detector(ctl_dev, *rcv_devs, timeout=tango_timeout_s)
        self._tango_timeout = tango_timeout_s

        try:
            # Get the topology property from control
            topology_prop = ctl_dev.get_property("receiver_topology")
            topology_name = topology_prop["receiver_topology"][0]
            self.topology_kind = TopologyKind(topology_name)
        except IndexError:
            # Property does not exist
            raise RuntimeError(
                "Could not find 'receiver_topology' property in control device"
            ) from None
        except ValueError as e:
            # Invalid topology (no matching name in Topology enum)
            raise ValueError(
                f"No matching topology type for {repr(topology_name)}"
            ) from e

        # Cache of processing pipelines by uuid (see method `pipeline`)
        self.__pipelines: dict[UUID, Pipeline] = {}

        self._tango_db = tg.Database()

    @classmethod
    def from_yaml(
        cls,
        config_filename: str = "l2c_config.yaml",
    ) -> "Client":
        with open(os.path.abspath(config_filename)) as config_f:
            # Raises on io/parsing error
            config = yaml.safe_load(config_f)

        ctl_url = config["ctl_url"]
        rcv_urls = config["rcv_urls"]

        ctl_dev = tg.DeviceProxy(ctl_url)
        rcv_devs = [tg.DeviceProxy(url) for url in rcv_urls]

        return Client(ctl_dev=ctl_dev, rcv_devs=rcv_devs)

    @property
    def control(self) -> tg.DeviceProxy:
        """Lima2 control tango device."""
        return self.detector.ctrl

    @property
    def receivers(self) -> list[tg.DeviceProxy]:
        """Lima2 receiver tango devices."""
        return self.detector.recvs

    @property
    def detector(self) -> Detector:
        return self._detector

    ####################################################################################
    # Detector properties
    ####################################################################################

    @property
    @docstring_from(Detector.det_info)
    def det_info(self) -> dict[str, Any]:
        return self.detector.det_info

    @property
    @docstring_from(Detector.det_status)
    def det_status(self) -> dict[str, Any]:
        return self.detector.det_status

    @property
    @docstring_from(Detector.det_capabilities)
    def det_capabilities(self) -> dict[str, Any]:
        return self.detector.det_capabilities

    @property
    @docstring_from(Detector.state)
    def state(self) -> state_machine.State:
        return self.detector.state

    @property
    @docstring_from(Detector.params_default)
    def params_default(self) -> dict[str, Any]:
        return self.detector.params_default

    @property
    @docstring_from(Detector.params_schema)
    def params_schema(self) -> dict[str, Any]:
        return self.detector.params_schema

    ####################################################################################
    # Control
    ####################################################################################

    def prepare_acq(
        self, uuid: UUID, ctrl_params: dict, acq_params: dict, proc_params: dict
    ) -> None:
        """Prepare acquisition with a given uuid and set of params."""

        # Adjust params for distributed acquisition
        ctl, acq, proc = topology.distribute_acq(
            ctl_params=ctrl_params,
            acq_params=acq_params,
            proc_params=proc_params,
            num_receivers=len(self.receivers),
        )

        self.detector.prepare_acq(uuid, ctl, acq, proc)

    @docstring_from(Detector.start_acq)
    def start_acq(self) -> None:
        self.detector.start_acq()

    @docstring_from(Detector.trigger)
    def trigger(self) -> None:
        self.detector.trigger()

    @docstring_from(Detector.stop_acq)
    def stop_acq(self) -> None:
        self.detector.stop_acq()

    @docstring_from(Detector.reset_acq)
    def reset_acq(self) -> None:
        self.detector.reset_acq()

    ####################################################################################
    # Progress counters
    ####################################################################################

    @property
    def nb_frames_acquired(self) -> ProgressCounter:
        """Number of frames acquired"""
        return ProgressCounter.from_single(
            SingleCounter(
                name="nb_frames_acquired",
                value=self.detector.nb_frames_acquired,
                source=self.control.name(),
            )
        )

    @property
    def nb_frames_xferred(self) -> ProgressCounter:
        """Aggregated number of frames transferred"""
        return progress_counter.aggregate(
            [
                SingleCounter(
                    name="nb_frames_xferred",
                    value=recv.nb_frames_xferred,
                    source=recv.name(),
                )
                for recv in self.receivers
            ]
        )

    ####################################################################################
    # Pipelines
    ####################################################################################

    @property
    def pipelines(self) -> list[UUID]:
        """Get list of available pipelines UUID"""
        uuids = [recv.pipelines for recv in self.receivers]
        if all(uuid is None for uuid in uuids):
            return []
        return [UUID(u) for u in {y for x in uuids for y in x}]

    def pipeline(self, uuid: Optional[UUID] = None) -> Pipeline:
        """Get a specific pipeline by uuid. Return the current one by default (uuid=None)"""
        if uuid is None:
            return self.current_pipeline

        if uuid in self.__pipelines:
            return self.__pipelines[uuid]

        # uuid isn't in the local cache of pipelines: get it from Tango
        instances = self._tango_db.get_device_exported(f"*/limaprocessing/{uuid}*")
        if not instances:
            raise ValueError(f"Pipeline not found in tango database: {uuid=}")

        class_names = [
            self._tango_db.get_device_info(instance).class_name
            for instance in instances
        ]

        # Select the pipeline according to the Tango class (Homogeneous processing for now)
        pipeline_class = pipelines.get_class(tango_class_name=class_names[0])

        # Note: sort list of pipeline instances by id in name such that pipelines are ordered
        # by receiver index. This is necessary e.g. in a strict round-robin topology to
        # be able to find a frame by index.
        def rcv_idx(name):
            return int(name.split("@")[1])

        proc_devs = [
            tg.DeviceProxy(url) for url in sorted(list(instances), key=rcv_idx)
        ]

        # Instantiate pipeline
        self.__pipelines[uuid] = pipeline_class(
            uuid, proc_devs, self.topology_kind, timeout=self._tango_timeout
        )

        return self.__pipelines[uuid]

    @property
    def current_pipeline(self) -> Pipeline:
        """Return the current pipeline as a Pipeline object.

        Raises if no pipeline has been created, or if receivers disagree on the
        current pipeline's uuid.
        """
        uuids: list[str] = [recv.current_pipeline for recv in self.receivers]

        if all([uuid == "" for uuid in uuids]):
            raise ValueError("No pipeline present in the tango database")

        uuids: list[UUID] = [UUID(uuid) for uuid in uuids]

        if not all([uuid == uuids[0] for uuid in uuids]):
            raise ValueError(f"Inconsistent pipeline uuids on all receivers: {uuids=}")

        return self.pipeline(uuids[0])

    def erase_pipeline(self, uuid: UUID) -> None:
        """Erase a pipeline instance.

        Raises if the pipeline is absent from a receiver.
        """
        for recv in self.receivers:
            recv.erasePipeline(str(uuid))
        self.__pipelines.pop(uuid, None)

    def clear_previous_pipelines(self) -> None:
        """Erase all pipelines except the current one."""
        try:
            current = self.current_pipeline.uuid
        except ValueError:
            current = None

        def clear(receiver: tg.DeviceProxy):
            """Clear previous pipelines from a single receiver device."""
            if receiver.pipelines is None:
                # No pipelines
                return []

            cleared = []
            for uuid in receiver.pipelines:
                if uuid != str(current):
                    logger.debug(
                        f"Erasing pipeline {uuid} from recv {receiver.dev_name()}"
                    )
                    receiver.erasePipeline(uuid)
                    cleared.append(uuid)
            return cleared

        cleared: set[str] = set()
        with futures.ThreadPoolExecutor(max_workers=len(self.receivers)) as executor:
            fs = [executor.submit(clear, receiver) for receiver in self.receivers]
            for receiver, future in zip(self.receivers, fs):
                try:
                    cleared = cleared.union(future.result())
                except Exception:
                    logger.warning(
                        f"Exception while clearing pipelines from {receiver.dev_name()}: "
                        f"{traceback.format_exc()}"
                    )

        logger.debug(f"Cleared pipelines {[uuid for uuid in set(cleared)]}")

        # Reset local cache
        if current is not None:
            self.__pipelines = {current: self.__pipelines[current]}

    ####################################################################################
    # Utility
    ####################################################################################

    def __repr__(self) -> str:
        return "\n".join(
            [
                "Lima2 client",
                f"State: {self.detector.state}",
                "Control:",
                f" {self.control}",
                f"Receivers ({self.topology_kind}):",
                *[f" - {recv}" for recv in self.receivers],
                "Progress counters:",
                f" - {self.nb_frames_acquired}",
                f" - {self.nb_frames_xferred}",
                str(self.current_pipeline) if self.__pipelines else "",
            ]
        )
