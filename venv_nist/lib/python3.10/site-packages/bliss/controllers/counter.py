# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

from bliss import global_map
from bliss.common.protocols import CounterContainer
from bliss.common.counter import Counter, CalcCounter
from bliss.common.utils import autocomplete_property
from bliss.scanning.chain import ChainNode
from bliss.scanning.acquisition.counter import SamplingCounterAcquisitionSlave
from bliss.scanning.acquisition.counter import IntegratingCounterAcquisitionSlave

from bliss.scanning.acquisition.calc import (
    CalcCounterChainNode,
    CalcCounterAcquisitionSlave,
)
from bliss.common.protocols import counter_namespace


class CounterController(CounterContainer):
    def __init__(self, name, master_controller=None, register_counters=True):

        self.__name = name
        self.__master_controller = master_controller
        self._counters = {}

        if register_counters:
            global_map.register(self, parents_list=["counters"])

    def _global_map_unregister(self):
        global_map.unregister(self)

    @property
    def name(self):
        return self.__name

    @property
    def fullname(self):
        if self._master_controller is None:
            return self.name
        else:
            return f"{self._master_controller.fullname}:{self.name}"

    @property
    def _master_controller(self):
        return self.__master_controller

    @autocomplete_property
    def counters(self):
        return counter_namespace(self._counters)

    def create_counter(self, counter_class, *args, **kwargs):
        counter = counter_class(*args, controller=self, **kwargs)
        return counter

    # --------------------- NOT IMPLEMENTED METHODS  ------------------------------------

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        """
        Returns an Acquisition object instance.

        This function is intended to be used through by the `ChainNode`.
        `acq_params`, `ctrl_params` and `parent_acq_params` have to be `dict` (`None` not supported)

        In case a incomplete set of `acq_params` is provided `parent_acq_params` may eventually
        be used to complete `acq_params` before choosing which Acquisition Object needs to be
        instantiated or just to provide all necessary `acq_params` to the Acquisition Object.

        parent_acq_params should be inserted into `acq_params` with low priority to not overwrite
        explicitly provided `acq_params` i.e. by using `setdefault`

        Example:

        .. code-block:: python

            if "acq_expo_time" in parent_acq_params:
                acq_params.setdefault("count_time", parent_acq_params["acq_expo_time"])
        """
        raise NotImplementedError

    def get_default_chain_parameters(self, scan_params, acq_params):
        """return completed acq_params with missing values guessed from scan_params
        in the context of default chain i.e. step-by-step scans"""
        raise NotImplementedError

    # --------------------- CUSTOMIZABLE METHODS  ------------------------------------
    def create_chain_node(self):
        return ChainNode(self)

    def get_current_parameters(self):
        """Should return an exhaustive dict of parameters that will be send
        to the hardware controller at the beginning of each scan.
        These parametes may be overwritten by scan specifc ctrl_params
        """
        return None

    def apply_parameters(self, parameters):
        pass


class SamplingCounterController(CounterController):
    def __init__(self, name, master_controller=None, register_counters=True):
        assert master_controller is not self
        super().__init__(
            name,
            master_controller=master_controller,
            register_counters=register_counters,
        )
        # by default maximum sampling frequency during acquisition loop = 1 Hz
        self.__max_sampling_frequency = 1

    @property
    def max_sampling_frequency(self):
        """Maximum sampling frequency in acquisition loop (Hz) (None -> no limit)"""
        return self.__max_sampling_frequency

    @max_sampling_frequency.setter
    def max_sampling_frequency(self, freq):
        """Maximum sampling acquisition frequency setter.

        freq = <int, float> -> set the frequency
        freq = None         -> means no limit (maximum frequency)
        """
        if freq and not isinstance(freq, (float, int)):
            raise ValueError("Max frequency should be a float number or None")
        if freq == 0:
            raise ValueError("Max frequency should be not zero")
        self.__max_sampling_frequency = freq

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        return SamplingCounterAcquisitionSlave(
            self, ctrl_params=ctrl_params, **acq_params
        )

    def get_default_chain_parameters(self, scan_params, acq_params):

        try:
            count_time = acq_params["count_time"]
        except KeyError:
            count_time = scan_params["count_time"]

        try:
            npoints = acq_params["npoints"]
        except KeyError:
            npoints = scan_params["npoints"]

        params = {"count_time": count_time, "npoints": npoints}

        return params

    def read_all(self, *counters):
        """Return the values of the given counters as a list.

        If possible this method should optimize the reading of all counters at once.
        """
        values = []
        for cnt in counters:
            values.append(self.read(cnt))
        return values

    def read(self, counter):
        """Return the value of the given counter"""
        raise NotImplementedError


class IntegratingCounterController(CounterController):
    def __init__(self, name="integ_cc", master_controller=None, register_counters=True):
        super().__init__(
            name,
            master_controller=master_controller,
            register_counters=register_counters,
        )

    def get_acquisition_object(self, acq_params, ctrl_params, parent_acq_params):
        return IntegratingCounterAcquisitionSlave(
            self, ctrl_params=ctrl_params, **acq_params
        )

    def get_default_chain_parameters(self, scan_params, acq_params):
        try:
            count_time = acq_params["count_time"]
        except KeyError:
            count_time = scan_params["count_time"]

        params = {"count_time": count_time}

        if self._master_controller is None:
            try:
                npoints = acq_params["npoints"]
            except KeyError:
                npoints = scan_params["npoints"]

            params["npoints"] = npoints

        return params

    def get_values(self, from_index, *counters):
        """
        Returns values from counters during the acquisition reading loop.

        This function is called by the acq_obj from a greenlet.
        It is triggered every 100ms until the `npoints` are fetched.

        If the function yet dont have the data, an empty list can be returned

        .. code-block:: python

            if not_ready:
                return [[]] * len(counters)

        Attributes:
            from_index: Index of the first scan point to retrive
            counters: The list of counters to read
        Returns:
            A list of list of data for each counters, each data must have the
            same size
        """
        raise NotImplementedError


class CalcCounterController(CounterController):
    def __init__(self, name, config, register_counters=True):

        super().__init__(name, register_counters=False)

        self._config = config
        self._input_counters = []
        self._output_counters = []
        self._counters = {}
        self._tags = {}

        self.build_counters(config)

        if register_counters:
            for counter in self.outputs:
                global_map.register(counter, parents_list=["counters"])

    def _global_map_unregister(self):
        for counter in self.outputs:
            global_map.unregister(counter)

    def get_acquisition_object(
        self, acq_params, ctrl_params, parent_acq_params, acq_devices
    ):
        return CalcCounterAcquisitionSlave(
            self, acq_devices, acq_params, ctrl_params=ctrl_params
        )

    def get_default_chain_parameters(self, scan_params, acq_params):
        if acq_params.get("npoints") is None:
            acq_params["npoints"] = scan_params["npoints"]

        return acq_params

    def create_chain_node(self):
        return CalcCounterChainNode(self)

    @property
    def tags(self):
        updated_tags = {}
        # update dictionary with aliases
        for name, cnt_tags in self._tags.items():
            alias = global_map.aliases.get_alias(name)
            if alias:
                updated_tags[alias] = cnt_tags
            updated_tags[name] = cnt_tags
        self._tags = updated_tags
        return self._tags

    def build_counters(self, config):
        """Build the CalcCounters from config.

        'config' is a dict with 2 keys: 'inputs' and 'outputs'.
        'config["inputs"]'  is a list of dict:  [{"counter":$cnt1, "tags": foo }, ...]
        'config["outputs"]' is a list of dict:  [{"name":out1, "tags": calc_data_1 }, ...]
        If the 'tags' is not found, the counter name will be used instead.
        """
        for cnt_conf in config.get("inputs"):
            cnt = cnt_conf.get("counter")
            if isinstance(cnt, Counter):
                tags = cnt_conf.get("tags", cnt.name)
                self._tags[cnt.name] = tags
                self._input_counters.append(cnt)
            else:
                raise ValueError(
                    f"CalcCounterController's input must be a counter but received: {cnt}"
                )

        for cnt_conf in config.get("outputs"):
            cnt_name = cnt_conf.get("name")
            if cnt_name:
                dim = cnt_conf.get("dim")
                if dim is None:
                    shape = cnt_conf.get("shape", tuple())
                else:
                    shape = cnt_conf.get("shape", (-1,) * dim)
                    if len(shape) != dim:
                        raise ValueError(
                            f"Wrong config for CalcCounterController's output '{cnt_name}': shape and dim mismatch (when using 'shape', 'dim' can be omitted)"
                        )

                unit = cnt_conf.get("unit")
                dtype = cnt_conf.get("dtype")
                cnt = CalcCounter(
                    cnt_name, controller=self, dtype=dtype, shape=shape, unit=unit
                )
                tags = cnt_conf.get("tags", cnt.name)
                self._tags[cnt.name] = tags
                self._output_counters.append(cnt)

    @property
    def inputs(self):
        return counter_namespace(self._input_counters)

    @property
    def outputs(self):
        return counter_namespace(self._output_counters)

    @autocomplete_property
    def counters(self):
        """Return all counters (i.e. the counters of this CounterController and sub counters)"""

        counters = {cnt.name: cnt for cnt in self.outputs}
        for cnt in self.inputs:
            counters[cnt.name] = cnt
            if isinstance(cnt, CalcCounter):
                counters.update(
                    {cnt.name: cnt for cnt in cnt._counter_controller.counters}
                )
        return counter_namespace(counters)

    def calc_function(self, input_dict):
        raise NotImplementedError


class SoftCounterController(SamplingCounterController):
    def __init__(self, name="soft_counter_controller", register_counters=True):
        super().__init__(name, register_counters=True)

    def read(self, counter):
        return counter.apply(counter.get_value())
