# This file is part of the Lima2 project
#
# Copyright (c) 2020-2024 Beamline Control Unit, ESRF
# Distributed under the MIT license. See LICENSE for more info.

"""Low level interface to the detector.

Typical usage example:

  import tango
  ctrl = tango.DeviceProxy(ctrl_dev_name)
  recvs = [tango.DeviceProxy(n) for n in recv_dev_names]

  det = Detector(ctrl, recvs)
  det.prepare_acq(...)
  det.start_acq(...)
"""

from __future__ import annotations

import json
import logging
import uuid

import tango
from beartype import beartype
from jsonschema_default import create_from
from typing_extensions import Optional

from lima2.client import pipelines
from lima2.client import state_machine as fsm
from lima2.client.state_machine import State
from lima2.client.utils import validate

# Create a logger
_logger = logging.getLogger(__name__)


class CommError(RuntimeError):
    """Communication error with the Detector interface."""

    pass


class Detector:
    """The main Detector interface."""

    def __init__(
        self,
        ctrl_dev: tango.DeviceProxy,
        *recv_devs: tango.DeviceProxy,
        timeout: int,
    ):
        """Construct a Detector object.

        Example:
            >>> ctrl = tango.DeviceProxy(ctrl_dev_name)
            >>> recvs = [tango.DeviceProxy(n) for n in recv_dev_names]
            >>> det = Detector(ctrl, recvs)

        Args:
            ctrl_dev: The control device instance name (aka domain/family/member)
            recv_devs: A list of receiver device instance names (aka domain/family/member)
            timeout: The tango device timeout [second]
        """
        # Preconditions
        if not recv_devs:
            raise ValueError("Must provide at least one receiver")

        self.__ctrl = ctrl_dev
        self.__recvs = list(recv_devs)

        self.ping()

        rank_order = [d.recv_rank for d in recv_devs]
        if not all([r == i for i, r in enumerate(rank_order)]):
            raise ValueError(
                f"Receiver MPI rank order does not match client order: {rank_order}"
            )

        self.__params_schema: Optional[dict] = None
        self.__fsm = fsm.StateMachine(self.__ctrl, self.__recvs)
        self.__try_sync_hard()

        if _logger.getEffectiveLevel() == logging.DEBUG:
            # Log transition of states
            def on_state_change(state):
                _logger.debug(f"on_state_change: transition to {state}")

            self.register_transition_logger(on_state_change)

        for d in self.devs:
            d.set_green_mode(tango.GreenMode.Gevent)
            d.set_timeout_millis(timeout * 1000)

    @property
    def det_info(self) -> dict:
        """
        A dict containing the detector static information such as make, model, serial number,
        detector and pixel dimensions...

        Example:

            >>> det.det_info
            {
                'plugin': 'Dectris',
                'model': 'Dectris EIGER2 CdTe 1M-W',
                'sn': 'E-02-0234',
                'pixel_size': {'x': 0.5, 'y': 0.5},
                'expo_time_range': [1, 10000000],
                'latency_time_range': [0, 1000000],
                'trigger_modes': ['internal', 'software'],
                'dimensions': {'x': 2048, 'y': 2048}
            }

        Group:
            Info
        """
        return json.loads(self.__ctrl.det_info)  # type: ignore

    @property
    def det_status(self) -> dict:
        """
        A dict containing the detector health information such as make, model, serial number,
        detector and pixel dimensions...

        Example:

            >>> det.det_status
            {
                'humidity': 2.78143310546875,
                'temperature': 27.421295166015625,
                'high_voltage_state': 'READY',
                'state': 'idle',
                'time': '2023-01-26T12:32:48+0100'
            }

        Group:
            Status
        """
        return json.loads(self.__ctrl.det_status)  # type: ignore

    @property
    def det_capabilities(self) -> dict:
        """
        A dict containing the detector capabilities...

        Example:

            >>> det.det_capabilities
            {
                'trigger_modes': ['internal', 'software']
            }

        Group:
            Info
        """
        return json.loads(self.__ctrl.det_capabilities)  # type: ignore

    def __get_params_schema(self) -> dict:
        """
        Get the parameters's schema for each devices from the Tango class attribute

        Raises tango.DevFailed if we cannot connect to database at TANGO_HOST.

        If the lima2 server hasn't been started yet, returns a dict of the form:
        {
            d.dev_name(): {"init_params": None, "acq_params": None}
            for d in self.devs
        }
        """
        tango_db = tango.Database()

        res = {}
        for d in self.devs:
            # Use DB only to get device class (device do not need to be exported)
            dev_name = d.dev_name()
            dev_class = tango_db.get_device_info(dev_name).class_name

            def get_schema(dev_class, param):
                prop = tango_db.get_class_attribute_property(dev_class, param)
                # Each attribute property is a StdStringVector with a single value
                if prop[param]:
                    return json.loads(prop[param]["schema"][0])

            res[dev_name] = {
                "init_params": get_schema(dev_class, "init_params"),
                "acq_params": get_schema(dev_class, "acq_params"),
            }

        return res

    @property
    def params_schema(self) -> dict:
        """Returns JSON schemas of init, acquisition and processing parameters.

        Example:

            >>> det.params_schema
            {
                'id00/limacontrol/simulator': {
                    'init_params': {
                        "$schema":"http://json-schema.org/draft-06/schema",
                        "$id":"https://example.com/lima2.schema.json",
                        "title":"init_params"
                    },
                    'acq_params': {...}
                }
                'id00/limareceiver/simulator@1': {
                    'init_params': None,
                    'acq_params': {...}
                },
                ...
            }

        Returns:
            A dict mapping parameters with their JSON schema for each devices.

        Group:
            Parameters
        """
        if self.__params_schema is None:
            self.__params_schema = self.__get_params_schema()

        return self.__params_schema

    @property
    def params_default(self) -> dict:
        """Returns a set of parameters with default values for init, acquisition and processing parameters.

        Example:

            >>> det.params_schema
            {
                'id00/limacontrol/simulator': {
                    'init_params': {
                        "$schema":"http://json-schema.org/draft-06/schema",
                        "$id":"https://example.com/lima2.schema.json",
                        "title":"init_params"
                    },
                    'acq_params': {...}
                }
                'id00/limareceiver/simulator@1': {
                    'init_params': None,
                    'acq_params': {...}
                },
                ...
            }

        Returns:
            A dict mapping parameters with their JSON schema for each devices.

        Group:
            Parameters
        """
        res = {}
        for key, value in self.params_schema.items():
            res[key] = {k: create_from(v) if v else None for k, v in value.items()}

        return res

    def ping(self):
        """
        Ping all the devices of the system.

        Raises:
            CommError if the connection failed.

        Group:
            Status
        """
        for d in self.devs:
            try:
                d.ping()
            except tango.DevFailed as e:
                raise CommError(f"Failed to ping device {d.dev_name()}") from e

    @property
    def devs(self) -> list[tango.DeviceProxy]:
        """
        The list of all the Tango devices.

        Group:
            Devices
        """
        return [self.__ctrl] + self.__recvs

    @property
    def ctrl(self) -> tango.DeviceProxy:
        """
        The control Tango device.

        Group:
            Devices
        """
        return self.__ctrl

    @property
    def recvs(self) -> list[tango.DeviceProxy]:
        """
        The receiver Tango devices.

        Group:
            Devices
        """
        return self.__recvs

    @property
    def state(self) -> State:
        """
        The current state of the client.

        Group:
            State
        """
        return self.__fsm.state

    def __try_sync_hard(self):
        """
        Try to init the state from a lazy constructed device Proxy
        (to be used when the DS is not running when Detector is constructed)
        """
        try:
            self.ping()
            self.sync_hard()
        except tango.DevFailed:
            return False

    def sync_hard(self):
        """
        Synchronize the current state of the client with the server states.

        Group:
            State
        """
        try:
            self.__fsm.sync()
        except Exception as e:
            _logger.warning(f"Unable to sync with server: {e}")

        return self.__fsm.state

    @property
    def dev_states(self):
        """
        A list of the individual states of the devices.

        Group:
            State
        """
        return [dev.acq_state for dev in self.devs]

    def register_transition_logger(self, logger):
        """
        Register a logger function to be notified on FSM transition.

        Args:
            logger: A callback with the following signature.

        Example:

            >>> def on_transition(source, target):
                    print(f"transition from {source} to {target}")
            >>> fsm.register_transition_logger(on_transition)

        Group:
            State
        """
        self.__fsm.register_transition_logger(logger)

    def unregister_transition_logger(self, logger):
        """
        Unregister a given transition logger function.

        Args:
            logger: A callback to unregister.

        Group:
            State
        """
        self.__fsm.unregister_transition_logger(logger)

    @beartype
    def prepare_acq(
        self, uuid: uuid.UUID, ctrl_params: dict, acq_params: list, proc_params: list
    ):
        """
        Prepare the acquisition with a given UUID.

        Args:
            uuid: The UUID associated with the acquisition
            ctrl_params: A dict of system acquisition params (per controller)
            acq_params: A list of acquisition params (per receivers)
            proc_params: A list of processing params (per receivers)

        Group:
            Control
        """
        _logger.debug(
            f"prepare_acq({uuid}, {ctrl_params}, {acq_params}, {proc_params})"
        )

        validate(
            instance=ctrl_params,
            schema=self.params_schema[self.ctrl.name()]["acq_params"],
        )

        for i, recv in enumerate(self.__recvs):
            validate(
                instance=acq_params[i],
                schema=self.params_schema[recv.name()]["acq_params"],
            )

            pipeline_class = pipelines.get_class(proc_params[i]["class_name"])
            validate(instance=proc_params[i], schema=pipeline_class.params_schema)

        # Update parameters on the device servers
        # TODO: Rename acq_params to ctrl_params
        self.__ctrl.acq_params = json.dumps(ctrl_params)
        for i, recv in enumerate(self.__recvs):
            recv.acq_params = json.dumps(acq_params[i])
            recv.proc_params = json.dumps(proc_params[i])

        res = self.__fsm.prepare(str(uuid))

        return res.get()  # Makes prepare_acq() synchronous

    def start_acq(self):
        """Start acquisition.

        Group:
            Control
        """
        self.__fsm.start()

    def trigger(self):
        """Software trigger.

        Group:
            Control
        """
        self.__fsm.trigger()

    def stop_acq(self):
        """
        Stop acquisition.

        Group:
            Control
        """
        self.__fsm.stop()

    def reset_acq(self):
        """
        Reset acquisition when the detector is in State.FAULT state

        Group:
            Control
        """
        self.__fsm.reset_acq()

    @property
    def ctrl_params(self):
        """
        Returns the current set of control params

        Group:
            Parameters
        """
        return json.loads(self.__ctrl.acq_params)

    @property
    def acq_params(self):
        """
        Returns the current set of receiver params

        Group:
            Parameters
        """
        return [json.loads(dev.acq_params) for dev in self.__recvs]

    @property
    def proc_params(self):
        """
        Returns the current set of processing params

        Group:
            Parameters
        """
        return [json.loads(dev.proc_params) for dev in self.__recvs]

    @property
    def nb_frames_acquired(self) -> int:
        """
        The number of acquired frames (on the detector side).

        Returns:
            The number of frames acquired

        Group:
            Status
        """
        return self.__ctrl.nb_frames_acquired  # type: ignore

    @property
    def nb_frames_xferred(self) -> list[int]:
        """
        The number of frames transferred (across all receivers).

        Returns:
            A list of number of frames transferred

        Group:
            Status
        """
        return [recv.nb_frames_xferred for recv in self.__recvs]

    # @contextmanager
    # @beartype
    # def get_processing_resource(self, uuid: uuid.UUID):
    #     """
    #     Yields a Processing instance given a uuid. Example:

    #     with det.get_processing_resource(uuid) as proc:
    #         print(f"counters: {proc.progress_counters()}")
    #         # Processing is erased when leaving this scope

    #     Returns:
    #         a Processing object.
    #     """
    #     try:
    #         yield self.get_processing(self, uuid)
    #     finally:
    #         self.erase_processing(uuid)
