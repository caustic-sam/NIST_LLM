# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from enum import Enum, unique
from typing_extensions import Union
import gevent
import logging
import numbers

from bliss import global_map, global_log
from bliss.common.tango import DeviceProxy
from bliss.common.utils import autocomplete_property, typecheck
from bliss.controllers.counter import CounterContainer, counter_namespace
from bliss.controllers.lima2.tabulate import tabulate
from bliss.controllers.lima2.controller import DetectorController
from bliss.controllers.lima2.settings import Settings, setting_property
from bliss.controllers.lima2.utils import lazy_init

from lima2.client import Client, State


_logger = logging.getLogger("bliss.ctrl.lima2")


# Logger decorator
def logger(fn):
    def inner(*args, **kwargs):
        _logger.debug(f"Entering {fn.__name__}")
        to_execute = fn(*args, **kwargs)
        _logger.debug(f"Exiting {fn.__name__}")
        return to_execute

    return inner


@unique
class ProcPlugin(Enum):
    LEGACY = "LimaProcessingLegacy"
    SMX = "LimaProcessingSmx"
    XPCS = "LimaProcessingXpcs"


@lazy_init
class Lima2(CounterContainer, Settings):
    """
    Lima2 device.
    Basic configuration:
        name: simulator
        class: Lima2
        tango_ctrl_url: id00/limacontrol/simulator
        tango_recv_urls:
          - id00/limareceiver/simulator_recv_1
          - id00/limareceiver/simulator_recv_2

        directories_mapping:
          default:              # Mapping name
            - path: /data/inhouse
              replace-with: /hz
            - path: /data/visitor
              replace-with: Z:/
          local:
            - path: /data/inhouse
              replace-with: L:/
    """

    TIMEOUT = 10.0

    DEVICE_TYPE = "lima2"

    @logger
    def __init__(self, config):
        """Lima2 device.

        name -- the controller's name
        config -- controller configuration
        in this dictionary we need to have:
        tango_ctrl_url -- tango (controller) device url
        tango_recv_urls -- tango (receivers) device urls
        optional:
        tango_timeout -- tango timeout (s)
        """

        _logger.debug(f"Initialize Lima2 {config['name']}")

        self._config = config
        tango_ctrl_url = config.get("tango_ctrl_url")
        tango_recv_urls = config.get("tango_recv_urls").raw_list
        self._server_urls = {
            "tango_ctrl_url": tango_ctrl_url,
            "tango_recv_urls": tango_recv_urls,
        }
        tango_ctrl_dev = DeviceProxy(tango_ctrl_url)
        tango_recv_devs = [DeviceProxy(url) for url in tango_recv_urls]

        self._det_info = None
        self._version = None
        self._acq_uuid = None  # Latest uuid successfully prepared
        self._proc_plugin = None
        self._processing = None
        self._detector = None
        self._frame_cc = None

        self.__client = None

        # Enable logs from the lima2.client package
        logging.getLogger("lima2.client").setLevel(global_log._LOG_DEFAULT_LEVEL)

        tango_ctrl_dev = DeviceProxy(self._server_urls["tango_ctrl_url"])
        tango_recv_devs = [
            DeviceProxy(url) for url in self._server_urls["tango_recv_urls"]
        ]

        self.__client = Client(ctl_dev=tango_ctrl_dev, rcv_devs=tango_recv_devs)

        params_default = self.__client.params_default

        # Create the user data structures to set detector params
        self._ctrl_params = params_default[tango_ctrl_url]["acq_params"]

        # TODO Fix heterogeneous receiver case (just a a reference to _ctrl_params for now)
        # self._recvs_params = [
        #     params_default[tango_recv_url]["acq_params"]
        #     for tango_recv_url in tango_recv_urls
        # ]
        self._recvs_params = self._ctrl_params

        # Single detector controller
        self._frame_cc = DetectorController(self)

        # Detector plugin
        try:
            import importlib

            plugin = self.det_info["plugin"].lower()
            module = importlib.import_module(__package__ + ".detectors" + f".{plugin}")

            self._detector = module.Detector(self)
        except ImportError:
            _logger.warning(f"could not find a plugin for detector {plugin}")

        # Acquisition UI
        self._acquisition = Acquisition(self)

        # Processing plugins
        self._processing_plugins = {}
        for plugin in list(ProcPlugin):
            proc = {}
            try:
                import importlib

                module = importlib.import_module(
                    __package__ + ".processings" + f".{plugin.name.lower()}"
                )

                if plugin not in self._processing_plugins:
                    proc[plugin.name] = module.Processing(self._config)
            except ImportError:
                _logger.warning(f"could not find a plugin for processing {plugin}")
            self._processing_plugins.update(proc)

        # Init counters of processing plugins
        for _, plugin in self._processing_plugins.items():
            plugin._init_with_device(self)

        # Master file generation
        self._master_file_generation = MasterFileGeneration(config=config)

        # Init Settings
        Settings.__init__(self, self._config)

        # Global map
        global_map.register("lima2", parents_list=["global"])
        global_map.register(
            self,
            parents_list=["lima2", "controllers", "counters"],
            children_list=[tango_ctrl_dev, *tango_recv_devs],
        )

    @property
    def _client(self) -> Client:
        return self.__client

    def _get_default_chain_counter_controller(self):
        """Return the default counter controller that should be used
        when this controller is used to customize the DEFAULT_CHAIN
        """
        return self._frame_cc

    # { required by AcquisitionObject
    @property
    def name(self):
        return self._config["name"]

    # }

    # { Implement CounterContainer
    @autocomplete_property
    def counters(self):
        cnts = list()
        if self._detector:
            cnts += self._detector.counters
        if self._processing:
            cnts += self._processing.counters
        return counter_namespace(cnts)

    @property
    def counter_groups(self):
        if self._detector and self._processing:
            groups = self._detector.counter_groups | self._processing.counter_groups
            groups["default"] = self.counters
            return counter_namespace(groups)
        else:
            return counter_namespace([])

    # }

    @autocomplete_property
    def acquisition(self):
        """The acquisition user interface"""
        return self._acquisition

    @autocomplete_property
    def processing(self):
        """The processing user interface"""
        return self._processing

    @autocomplete_property
    def detector(self):
        """The detector (specific) user interface"""
        return self._detector

    @autocomplete_property
    def master_file_generation(self):
        return self._master_file_generation

    def __info__(self):
        try:
            res = f"{self.det_info['plugin']} ({self.det_info['model']})\n\n"
            res += "Status:\n" + tabulate(self.det_status) + "\n\n"
            # res += "Accumulation:\n" + tabulate(ctrl_params["accu"]) + "\n\n"
            res += "Acquisition:\n" + self._acquisition.__info__() + "\n\n"
            res += "Detector:\n\n" + self._detector.__info__() + "\n\n"
            res += "Processing:\n\n" + self._processing.__info__()
        except Exception as e:
            res = str(e)

        return res

    @property
    def det_info(self):
        return self._client.det_info

    @property
    def det_status(self):
        return self._client.det_status

    @property
    def det_capabilities(self):
        return self._client.det_capabilities

    @setting_property(default="legacy")
    def proc_plugin(self) -> ProcPlugin:
        return self._proc_plugin

    @proc_plugin.setter
    def proc_plugin(self, plugin: Union[str, ProcPlugin]):
        if isinstance(plugin, str):
            self._proc_plugin = ProcPlugin[plugin.upper()]
            self._processing = self._processing_plugins[plugin.upper()]
        else:
            self._proc_plugin = plugin
            self._processing = self._processing_plugins[plugin.name]

            # TODO: Construct processings for each receivers (currently same processing)

            # self._processing = [
            #     self._processing_plugins[i][self.proc_plugin.name]
            #     for i, _ in enumerate(self._det.recvs)
            # ]
        return self._proc_plugin  # Required by Settings

    @property
    def state(self) -> State:
        return self._client.state

    @property
    def nb_frames_acquired(self) -> int:
        return self._client.nb_frames_acquired.sum

    @property
    def nb_frames_xferred(self) -> int:
        return self._client.nb_frames_xferred.sum

    @logger
    def prepare(
        self,
        uuid,
        ctrl_params: dict = None,
        recvs_params: dict = None,
        procs_params: dict = None,
    ):
        if ctrl_params is None:
            ctrl_params = self._ctrl_params

        if recvs_params is None:
            recvs_params = self._recvs_params

        if procs_params is None:
            procs_params = self._processing._params

        # Append the class_name to the proc_params dict
        procs_params["class_name"] = self.proc_plugin.value

        res = self._client.prepare_acq(uuid, ctrl_params, recvs_params, procs_params)
        assert self.state == State.PREPARED

        # Enable master file generation
        if self.master_file_generation.enabled:
            import lima2.master_file.generate as master_file

            # Enable info messages from the generation process
            logging.getLogger("lima2.master_file").setLevel(logging.INFO)

            master_file.enable_generation(
                pipeline=self._client.current_pipeline,
                num_frames=ctrl_params["acq"]["nb_frames"],
                proc_params=procs_params,
                rr_offset=self.master_file_generation.rr_offset,
            )

        self._acq_uuid = uuid

        return res

    @logger
    def start(self):
        self._client.start_acq()

        # TODO should be removed when start_acq is synchronous
        while self.state != State.RUNNING:
            gevent.sleep(0.01)

    @logger
    def trigger(self):
        self._client.trigger()

    @logger
    def stop(self):
        # Check needed since Bliss calls stopAcq systematically even when not necessary
        if self.state == State.RUNNING:
            self._client.stop_acq()

    @logger
    def reset(self):
        if self.state == State.FAULT:
            self._client.reset_acq()

    @property
    def current_pipeline(self):
        # _logger.debug(f"UUID {self._acq_uuid}")
        return self._client.current_pipeline

    @property
    def pipelines(self):
        return self._client.pipelines


# Acquisition user interface
class Acquisition(Settings):
    """
    Acquisition settings common to all detectors
    """

    def __init__(self, device):
        self._device = device
        self._params = device._ctrl_params["acq"]
        super().__init__(device._config)

    @setting_property(default=1)
    def nb_frames(self):
        return self._params["nb_frames"]

    @nb_frames.setter
    @typecheck
    def nb_frames(self, value: numbers.Integral):
        if value < 0:
            raise ValueError("nb_frames < 0")
        self._params["nb_frames"] = value

    @setting_property(default="internal")
    def trigger_mode(self):
        return self._params["trigger_mode"]

    @trigger_mode.setter
    @typecheck
    def trigger_mode(self, value: str):
        range = self._device.det_capabilities.get(
            "trigger_modes", ["internal", "software"]
        )
        if value not in range:
            raise ValueError(f"Out of range {range}")
        self._params["trigger_mode"] = value

    @setting_property(default=1.0)
    def expo_time(self):
        value_us = self._params["expo_time"]
        return value_us * 1e-6

    @expo_time.setter
    @typecheck
    def expo_time(self, value: numbers.Number):
        range = self._device.det_capabilities.get("expo_time_range", [1000, 100000000])
        value_us = int(value * 1e6)
        if not (range[0] <= value_us and value_us < range[1]):
            raise ValueError(
                f"Out of range ({range[0]} <= expo_time={value_us} < {range[1]})"
            )
        self._params["expo_time"] = value_us

    @setting_property(default=1.0)
    def latency_time(self):
        value_us = self._params["latency_time"]
        return value_us * 1e-6

    @latency_time.setter
    @typecheck
    def latency_time(self, value: numbers.Number):
        range = self._device.det_capabilities.get(
            "latency_time_range", [1000, 100000000]
        )
        value_us = int(value * 1e6)
        if not (range[0] <= value_us and value_us < range[1]):
            raise ValueError(
                f"Out of range ({range[0]} <= latency_time={value_us} < {range[1]})"
            )
        self._params["latency_time"] = value_us

    def __info__(self):
        return "Acquisition:\n" + tabulate(self._params) + "\n\n"


class MasterFileGeneration(Settings):
    """Master file generation user interface"""

    def __init__(self, config):
        self._enabled: bool
        self._rr_offset: int
        super().__init__(config=config, path=["master_file_generation"])

    @setting_property(default=False)
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    @typecheck
    def enabled(self, value: bool):
        self._enabled = value

    @setting_property(default=0)
    def rr_offset(self) -> int:
        return self._rr_offset

    @rr_offset.setter
    @typecheck
    def rr_offset(self, value: int):
        self._rr_offset = value

    def __info__(self):
        return (
            "Master file generation:\n"
            + tabulate(
                {
                    "enabled": self.enabled,
                    "rr_offset": self.rr_offset,
                }
            )
            + "\n\n"
        )
