# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Vacuum gauge controller.

Example yml file:

.. code-block:: yaml

    -
      # pirani gauge
      plugin: bliss
      name: pir121
      class: VacuumGauge
      uri: id43/v-pir/121
    -
      # penning gauge
      plugin: bliss
      name: pen121
      class: VacuumGauge
      uri: id43/v-balzpen/121

Test examples:

.. code-block::

    RH [1]: pir121.state
    Out [1]: 'ON'

    CYRIL [2]: pen71
      Out [2]:
               ----------------  id42/v-pen/71 ---------------
               State: ON
               Gauge is ON  -  Channel A1 (1)
               Rel. | Lower | Upper | SA | State
                 1  | 1.5e-6| 5.0e-6|  1 |  ON
                 2  | 4.0e-3| 6.0e-3|  2 |  ON
                 3  | 1.0e-6| 3.0e-6|  3 |  ON
                 4  | 4.0e-3| 6.0e-3|  4 |  ON
                 A  | 4.0e-3| 1.0e-5|  6 |  ON
                 B  | 4.0e-3| 1.0e-5|  8 |  ON

               Failed to connect to device sys/hdb-push/id42
               The connection request was delayed.
               The last connection request was done less than 1000 ms ago
               -------------------------------------------------
               PRESSURE: 2.30e-07
               -------------------------------------------------

    RH [3]: pir121.pressure
    Out [3]: 0.0007999999797903001
"""

from gevent import sleep, Timeout
from bliss import global_map
from bliss.common.tango import DeviceProxy, DevFailed
from bliss.common.protocols import CounterContainer
from bliss.controllers.counter import counter_namespace
from bliss.controllers.tango_attr_as_counter import (
    TangoCounterController,
    TangoAttrCounter,
)


class VacuumGauge(CounterContainer):
    """Control VacGaugeServer Tango device server gauges"""

    def __init__(self, name, config):
        tango_uri = config.get("uri")
        self.__name = name
        self.__config = config
        self.proxy = DeviceProxy(tango_uri)
        global_map.register(self, children_list=[self.proxy], tag=f"VacuumGauge:{name}")

        cnt_controller = TangoCounterController(
            name, self.proxy, global_map_register=False
        )
        cnt_config = config.clone()
        cnt_config["attr_name"] = "pressure"
        cnt_config["mode"] = "SINGLE"
        self.__counter = TangoAttrCounter("pressure", cnt_config, cnt_controller)

    @property
    def name(self):
        """A unique name"""
        return self.__name

    @property
    def config(self):
        """Config of vacuum gauge"""
        return self.__config

    @property
    def counters(self):
        return counter_namespace((self.__counter,))

    @property
    def state(self):
        """Read the state (class tango.DevState).

        Available PyTango states:
            'ALARM', 'CLOSE', 'DISABLE', 'EXTRACT', 'FAULT', 'INIT', 'INSERT',
            'MOVING', 'OFF', 'ON', 'OPEN', 'RUNNING', 'STANDBY', 'UNKNOWN'.

        Returns:
            (str): The state.
        Raises:
            RuntimeError: Error from the device server
        """
        try:
            return self.proxy.state().name
        except (DevFailed, AttributeError) as err:
            raise RuntimeError(f"Error from {self.proxy.dev_name()}") from err

    @property
    def _tango_state(self):
        """Obsolete, but kept for compliance"""
        return self.state

    @property
    def status(self) -> str:
        """Return the Tango status string.

        Raises:
            DevFailed: Communication error with the device server
        """
        return self.proxy.status()

    def __info__(self):
        info_str = f" \n----------- {self.proxy.dev_name()} -----------\n"
        info_str += self.status.rstrip("\n") + "\n"
        info_str += "-------------------------------------\n"
        info_str += f"STATE: {self.state}\n"
        info_str += f"PRESSURE: {self.pressure:1.2e}\n"
        info_str += "-------------------------------------\n"
        return info_str

    @property
    def pressure(self):
        """Read the pressure value.

        Returns:
            (float): The pressure [mBar]
        """
        try:
            return self.proxy.pressure
        except DevFailed:
            return 0.0

    def set_on(self, timeout=None):
        """Turn the gauge on.

        Args:
            timeout(float): Timeout to wait the command to finish [s].
                            If None, wait forever
        """

        self.proxy.On()
        self._wait("ON", timeout)

    def set_off(self, timeout=None):
        """Turn the gauge off.

        Args:
            timeout(float): Timeout to wait the command to finish [s].
                            If None, wait forever
        """
        self.proxy.Off()
        self._wait("OFF", timeout)

    def _wait(self, state, timeout=None):
        """Wait execution to finish.

        Args:
            (str): state
            (float): timeout [s].
        Raises:
            RuntimeError: Execution timeout.
        """
        with Timeout(timeout, RuntimeError("Execution timeout")):
            while self.state != state:
                sleep(0.5)
