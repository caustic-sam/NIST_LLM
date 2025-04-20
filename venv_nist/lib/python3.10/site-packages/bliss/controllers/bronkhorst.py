# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
BLISS controller for Bronkhorst Mass flow Controller.
"""

import logging
import tabulate

from bliss import global_map
from bliss.controllers.tango_attr_as_counter import (
    TangoCounterController,
    TangoAttrCounter,
)

from bliss.comm.util import get_tango_proxy
from bliss.common import tango
from bliss.common.protocols import CounterContainer, HasMetadataForScan
from bliss.common.utils import autocomplete_property
from bliss.controllers.counter import counter_namespace

_logger = logging.getLogger("bliss.ctrl.bronkhorst")


def logger(func):
    """Logger decorator"""

    def inner(*args, **kwargs):
        _logger.debug("Entering %s", func.__name__)
        to_execute = func(*args, **kwargs)
        _logger.debug("Exiting %s", func.__name__)
        return to_execute

    return inner


class BronkhorstNode(TangoCounterController):
    """
    Bronkhorst SubDevice (Node) Controller
    """

    def __init__(self, name, tango_uri_or_proxy):
        super().__init__(name, tango_uri_or_proxy, global_map_register=False)

    @autocomplete_property
    def idn(self):
        return self._proxy.idn

    @autocomplete_property
    def type(self):
        return self._proxy.type

    @autocomplete_property
    def fluid(self):
        return self._proxy.fluid

    @autocomplete_property
    def measure(self):
        return self._proxy.measure

    @autocomplete_property
    def setpoint(self):
        return self._proxy.setpoint

    @setpoint.setter
    def setpoint(self, value):
        self._proxy.setpoint = value

    @autocomplete_property
    def fluid_temperature(self):
        return self._proxy.fluid_temperature

    @autocomplete_property
    def orifice(self):
        return self._proxy.orifice

    @autocomplete_property
    def pressure_inlet(self):
        return self._proxy.pressure_inlet

    @autocomplete_property
    def pressure_outlet(self):
        return self._proxy.pressure_outlet

    def __info__(self):
        info = f"Fluid   : {self.fluid}\n"
        info += f"Setpoint: {self.setpoint:.2f} %\n"
        info += f"Measure : {self.measure:.2f} %\n"
        # mystr += f"Flow    : {self.flow:.2f} ml/min\n"
        return info


class Bronkhorst(CounterContainer, HasMetadataForScan):
    """
    Bronkhorst Device Controller
    """

    def __init__(self, name, config):
        # super().__init__(config, share_hardware=False)
        global_map.register(self, tag=name)

        nodes_config = config.get("nodes")

        self._name = name
        self._proxy = get_tango_proxy(config)
        self._subdevices = self._proxy.subdevices
        self._nodes = []

        self.__counters = []

        for subdevice in self._subdevices:
            try:
                proxy = tango.DeviceProxy(subdevice)

                # ??? node identificator ?
                if proxy.idn not in nodes_config:
                    _logger.warning(f"missing configuration for {proxy.idn}")
                    continue

                node_config = nodes_config.get(proxy.idn)

                node = BronkhorstNode(node_config["name"], proxy)
                self._nodes.append(node)
                setattr(self, node_config["name"], node)

                counter_config = config.clone()
                counter_config["uri"] = config["tango_url"]
                counter_config["attr_name"] = "measure"
                counter_config["mode"] = "SINGLE"
                counter_config["format"] = node_config.get("format", "%6.2f")
                cnt = TangoAttrCounter(node_config["name"], counter_config, node)

                # NB: "cnt.allow_failure==True"
                #     means "the scan will crash if reading of cnt fails"
                cnt.allow_failure = False
                self.__counters.append(cnt)
            except Exception:
                global_map.unregister(self)  # issue 3029
                raise

    @autocomplete_property
    def counters(self):
        return counter_namespace(self.__counters)

    def _set_node_attr(self, node):
        setattr(self, node._fullname, node)

    def _del_node_attr(self, node):
        delattr(self, node._fullname)

    def __info__(self):
        line1 = [""] + [node.name for node in self._nodes]
        line2 = ["Fluid"] + [node.fluid for node in self._nodes]
        line3 = ["Setpoint %"] + [f"{node.setpoint:.2f}" for node in self._nodes]
        line4 = ["Measure %"] + [f"{node.measure:.2f}" for node in self._nodes]
        info = tabulate.tabulate([line1, line2, line3, line4], tablefmt="plain")
        info += "\n"
        return info

    # def _get_bronkhorst_node(self, node_config, controller, index):
    #     node = BronkhorstNode(node_config, controller=controller, index=index)
    #     return node

    def scan_metadata(self):
        """
        Return a dict to be put in HDF5 metadata.
        """
        meta_data = {}
        for chan, node in self._nodes.items():
            if node._enabled.get():
                meta_data[node._fullname] = node._get_meta_data()
        return meta_data
