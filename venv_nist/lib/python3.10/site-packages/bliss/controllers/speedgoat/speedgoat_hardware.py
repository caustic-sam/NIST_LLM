# -*- coding: utf-8 -*-
#
# This file is part of the mechatronic project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Speedgoat Real Time Controller
"""

import collections
import treelib
import functools

from bliss.comm import rpc

from bliss.controllers.speedgoat.speedgoat_parameter import (
    SpeedgoatHdwParameterController,
)
from bliss.controllers.speedgoat.speedgoat_signal import SpeedgoatHdwSignalController
from bliss.controllers.speedgoat.speedgoat_scope import (
    SpeedgoatHdwScopeController,
    SpeedgoatHdwFastdaqController,
    SpeedgoatHdwRingBufferController,
    SpeedgoatHdwDisplScopeController,
    SpeedgoatHdwdaqBufferController,
)
from bliss.controllers.speedgoat.speedgoat_motor import SpeedgoatHdwMotorController
from bliss.controllers.speedgoat.speedgoat_counter import SpeedgoatHdwCounterController
from bliss.controllers.speedgoat.speedgoat_regul import SpeedgoatHdwRegulController
from bliss.controllers.speedgoat.speedgoat_generator import (
    SpeedgoatHdwGeneratorController,
)
from bliss.controllers.speedgoat.speedgoat_filter import SpeedgoatHdwFilterController
from bliss.controllers.speedgoat.speedgoat_trigger import SpeedgoatHdwTriggerController
from bliss.controllers.speedgoat.speedgoat_lut import SpeedgoatHdwLutController
from bliss.controllers.speedgoat.speedgoat_utils import SpeedgoatUtils


class SpeedgoatHdwController:
    def __init__(self, name, config):
        # Set the Speedgoat Name
        self.name = name

        # Get the RPC client object
        url = self._to_zerorpc_url(config["url"], default_port=config["port"])
        self._conn = rpc.Client(url)
        # Initialize all Speedgoat elements
        self.scope = SpeedgoatHdwScopeController(self)
        self.tgscope = SpeedgoatHdwDisplScopeController(self)
        self.fastdaq = SpeedgoatHdwFastdaqController(self)
        self.ringbuffer = SpeedgoatHdwRingBufferController(self)
        self.daqbuffer = SpeedgoatHdwdaqBufferController(self)
        self.motor = SpeedgoatHdwMotorController(self)
        self.counter = SpeedgoatHdwCounterController(self)
        self.regul = SpeedgoatHdwRegulController(self)
        self.generator = SpeedgoatHdwGeneratorController(self)
        self.filter = SpeedgoatHdwFilterController(self)
        self.trigger = SpeedgoatHdwTriggerController(self)
        self.lut = SpeedgoatHdwLutController(self)
        self.utils = SpeedgoatUtils(self)

        # Get Sampling Time of Speedgoat
        self._Ts = self._conn.get_sample_time()
        self._Fs = 1.0 / self._Ts

    def _reload_model_objects(self):
        """Function used to reload all objects"""
        self.parameter._load(force=True)
        self.signal._load(force=True)
        self.scope._load(force=True)
        self.tgscope._load(force=True)
        self.fastdaq._load(force=True)
        self.ringbuffer._load(force=True)
        self.motor._load(force=True)
        self.counter._load(force=True)
        self.regul._load(force=True)
        self.generator._load(force=True)
        self.filter._load(force=True)
        self.trigger._load(force=True)
        self.lut._load(force=True)

    @functools.cached_property
    def parameter(self):
        return SpeedgoatHdwParameterController(self)

    @functools.cached_property
    def signal(self):
        return SpeedgoatHdwSignalController(self)

    @property
    def _app_name(self):
        return self._conn.get_app_name()

    @property
    def _is_app_running(self):
        return self._conn.is_app_running()

    @property
    def _is_overloaded(self):
        return self._conn.is_overloaded()

    def __getattr__(self, name):
        server_call = getattr(self._conn, name)

        def func(*args):
            return server_call(*args)

        func.__name__ = name
        setattr(self, name, func)
        return func

    def _to_zerorpc_url(self, url, default_port=None):
        """Get the correctly formated string used to connect to host computer

        :url: URL used to connect to the Host Machine (e.g. PCSPEEDGOAT:8200)
        :default_port: If the port is not present in the url, a default value can be used
        :returns: Return a correctly formated string to connect to the host machine,
                  for instance 'tcp://PCSPEEDGOAT:8200'
        """
        # Get host and port
        pars = url.rsplit(":", 1) if isinstance(url, str) else url
        host = pars[0]
        port = int(pars[1]) if len(pars) > 1 else default_port

        # Add TCP if not already present in host
        if "://" not in host:
            host = "tcp://" + host

        # Return correctly formated string
        return f"{host}:{port}"

    """
    Speedgoat Tree Tools
    """

    def _get_all_objects_from_key(self, key):
        objs = []
        for param in self.parameter._params:
            if param["block"].rfind(key) != -1:
                objs.append(param["block"][0 : param["block"].rindex(key) - 1])
        return objs

    def _create_block_dict(self, infos):
        """
        A dictionary of blocks. Key is block name and value is a parameter/signal
        dictionary.
        """
        blocks = collections.defaultdict(dict)
        for info in infos:
            blocks[info["block"]][info["name"]] = info
        return blocks

    def _create_tree(self, infos, param_leaf=True):
        """infos: obtained from speedgoat.get_param_infos()
        or speedgoat.get_signal_infos()
        """
        tree = treelib.Tree()
        root = tree.create_node("", "")
        for block, params in self._create_block_dict(infos).items():
            parent, block_path = root, []
            for item in block.split("/"):
                if item:
                    block_path.append(item)
                block_name = "/".join(block_path)
                node = tree.get_node(block_name)
                if node is None:
                    node = tree.create_node(item, block_name, parent)
                parent = node
            for param_name, param_info in params.items():
                puid = f"{param_info['block']}/{param_info['name']}"
                param_node = tree.create_node(param_name, puid, parent, param_info)
                if not param_leaf:
                    for pname, pvalue in param_info.items():
                        piuid = puid + "." + pname
                        pilabel = f"{pname} = {pvalue}"
                        tree.create_node(pilabel, piuid, param_node, pvalue)
        return tree
