# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Controller class

Example of .yml file for a Vacuubrand pressure transmitter DCP3000
with the mandatory fields:

.. code-block::

- plugin: generic
  class: Dcp3000
  name: dcp1
  serial:
    url: /dev/ttyS4
    baudrate: 19200


  counters:
    - name: dcp1_c
"""


from bliss import global_map
from bliss.comm.util import get_comm
from bliss.common.utils import autocomplete_property
from bliss.common.greenlet_utils import protect_from_kill
from bliss.controllers.counter import SamplingCounterController
from bliss.common.counter import SamplingCounter  # noqa: F401
from bliss.controllers.bliss_controller import BlissController


class DcpCounterController(SamplingCounterController):
    def __init__(self, name, dcp):
        super().__init__(name, register_counters=False)
        self.dcp = dcp

    def read(self, counter):
        return self.dcp.comm("IN_PV_1")


class Dcp3000(BlissController):
    def __init__(self, config):
        super().__init__(config)

        # Communication
        if "baudrate" in config["serial"]:
            _baudrate = config["serial"]["baudrate"]
        else:
            _baudrate = 19200

        self._cnx = get_comm(config, baudrate=_baudrate, timeout=3)
        global_map.register(
            self, parents_list=["controllers", "counters"], children_list=[self._cnx]
        )

        # default config
        self._default_config = config.get("default_config", None)

        # Counters
        self._counters_controller = DcpCounterController(self.name, self)

        self._counters_controller.max_sampling_frequency = config.get(
            "max_sampling_frequency"
        )

    def _get_subitem_default_class_name(self, cfg, parent_key):
        if parent_key == "counters":
            return "SamplingCounter"

    def _create_subitem_from_config(
        self, name, cfg, parent_key, item_class, item_obj=None
    ):
        if parent_key == "counters":
            return item_class(name, self._counters_controller)

    def _load_config(self):
        pass

    @protect_from_kill
    def comm(self, msg, timeout=None, text=True):
        """
        Serial Communication
        """
        self._cnx.open()
        ret = None
        with self._cnx._lock:
            # self._cnx._write((msg + "\r\n").encode())
            msg = (msg + "\r\n").encode()
            ans = self._cnx.write_readline(msg, eol="\r\n", timeout=timeout).split()
            ret = float(ans[0].decode())
        return ret

    @autocomplete_property
    def counters(self):
        """
        DCP3000 counters
        """
        return self._counters_controller.counters
