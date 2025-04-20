# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Attocube IDS 3010 interferometer controller.
Based on Attocube sdk.
"""


import time
import socket
import numpy as np
from tabulate import tabulate
import enum
import textwrap

import attocube_ids
from attocube_ids import ACS

from bliss import global_map
from bliss.common.logtools import log_debug
from bliss.controllers.counter import SamplingCounterController
from bliss.common.counter import SamplingCounter, SamplingMode
from bliss.common.protocols import CounterContainer
from bliss.common.utils import autocomplete_property

# TODO : metadata emission ?

# TODO : patch ACS.py to be greeenlet proof ?

# Bug in case of ctrl-C after a loop


INIT_MODES = {0: "High Accuracy Initialization", 1: "Quick Initialization"}

MODE = enum.Enum(
    "Mode", "IDLE ALIGN MEASURE MEASURE_STARTING ALIGN_STARTING TEST_CHANNELS"
)

MODE_TO_STR = {
    MODE.ALIGN: "optics alignment running",
    MODE.IDLE: "system idle",
    MODE.MEASURE: "measurement running",
    MODE.MEASURE_STARTING: "measurement starting",
    MODE.ALIGN_STARTING: "optics alignment starting",
    MODE.TEST_CHANNELS: "test channels enabled",
}

STR_TO_MODE = {
    "optics alignment running": MODE.ALIGN,
    "system idle": MODE.IDLE,
    "measurement running": MODE.MEASURE,
    "measurement starting": MODE.MEASURE_STARTING,
    "optics alignment starting": MODE.ALIGN_STARTING,
    "test channels enabled": MODE.TEST_CHANNELS,
}

STARTING_DURATION = {MODE.ALIGN: 5, MODE.MEASURE: 35}


class Device(ACS.Device):
    """
    bla
    """

    def __init__(self, address):
        super().__init__(address)

        self.about = attocube_ids.About(self)
        self.adjustment = attocube_ids.Adjustment(self)
        self.axis = attocube_ids.Axis(self)
        self.displacement = attocube_ids.Displacement(self)
        self.ecu = attocube_ids.Ecu(self)
        self.manual = attocube_ids.Manual(self)
        self.network = attocube_ids.Network(self)
        self.nlc = attocube_ids.Nlc(self)
        self.pilotlaser = attocube_ids.Pilotlaser(self)
        self.realtime = attocube_ids.Realtime(self)
        self.system = attocube_ids.System(self)
        self.system_service = attocube_ids.System_service(self)
        self.update = attocube_ids.Update(self)


class Ids3010SCC(SamplingCounterController):
    """
    IDS 3010 SamplingCounterController defined for specific reading of the device.
    """

    def __init__(self, parent):
        self.parent = parent

        super().__init__(self.parent.name)

    def read_all(self, *counters):
        """
        Return a list of all counters values read from device.
        """
        measure_dict = self.parent.get_measurements()

        values = []

        for cnt in counters:
            values.append(measure_dict[cnt.name])
        return values


class Ids3010(CounterContainer):
    """
    inheriting from (sampling)CounterContainer
    * means: there are counters inside.
    * implies:  implements a  .counters() method
    """

    def __init__(self, name, config):

        self.config = config
        self.name = name

        # super().__init__(config)
        self.host = config.get("host")

        self._sampling_counter_controller = Ids3010SCC(self)

        # Counters names hard-coded for now...
        for counter_name in ["d1", "d2", "d3", "c1", "c2", "c3"]:
            self._sampling_counter_controller.create_counter(
                SamplingCounter, counter_name, mode=SamplingMode.SINGLE
            )

        global_map.register(self, parents_list=["controllers"])

        try:
            log_debug(self, "Connecting to %s", self.host)
            self.dev = Device(self.host)
            self.dev.connect()
        except socket.gaierror as sock_exc:
            raise RuntimeError(f"Cannot connect to IDS3010:{self.host}") from sock_exc

    @property
    def mode(self):
        """
        Return mode in MODE.{IDLE ALIGN MEASURE MEASURE_STARTING ALIGN_STARTING TEST_CHANNELS}
        Raise TypeError in case of unknown mode.
        """
        mode_str = self.dev.system.getCurrentMode()
        try:
            mode = STR_TO_MODE[mode_str]
        except KeyError:
            raise TypeError(f"unknown mode: {mode_str}")

        return mode

    def stop(self):
        """
        Stop alignement or measurement.
        Raise RuntimeError if not in IDLE mode at the end.
        """
        mode = self.mode

        _t0 = time.time()
        if mode == MODE.ALIGN:
            self.dev.system.stopOpticsAlignment()
        elif mode == MODE.MEASURE:
            self.dev.system.stopMeasurement()
        elif mode == MODE.IDLE:
            print(f"Current mode is: {self.mode}")
            return
        print(f"Current mode is: {self.mode}")
        time.sleep(0.1)

        print("wait for idle")
        while self.mode != MODE.IDLE:
            print(".", end="", flush=True)
            if (time.time() - _t0) > 10:
                mode = self.mode
                raise RuntimeError(
                    f"Error: not yet in idle mode after 10 seconds...(current mode is: {mode})"
                )
            time.sleep(0.5)

        print(f"Current mode is: {self.mode}")

    def get_measurements(self):
        """
        Read displacement or contrast, depending on mode, for all 3 axes.

        Return a dict with keys: "dx" "cx" "bx" "wx" "mx", x in [1..3]

        Values are np.nan if they can not be read.
        """

        mode = self.mode

        disp1, disp2, disp3 = (np.nan, np.nan, np.nan)
        contrast1, contrast2, contrast3 = (np.nan, np.nan, np.nan)
        baseline1, baseline2, baseline3 = (np.nan, np.nan, np.nan)
        warn1, warn2, warn3 = (np.nan, np.nan, np.nan)
        mixc1, mixc2, mixc3 = (np.nan, np.nan, np.nan)

        if mode == MODE.MEASURE:
            _, disp1, disp2, disp3 = self.dev.displacement.getAxesDisplacement()

        elif mode == MODE.ALIGN:

            (
                warn1,
                contrast1,
                baseline1,
                mixc1,
            ) = self.dev.adjustment.getContrastInPermille(0)
            (
                warn2,
                contrast2,
                baseline2,
                mixc2,
            ) = self.dev.adjustment.getContrastInPermille(1)
            (
                warn3,
                contrast3,
                baseline3,
                mixc3,
            ) = self.dev.adjustment.getContrastInPermille(2)

        elif mode == MODE.IDLE:
            return None
        else:
            raise RuntimeError(f"Not in a good mode: {mode}")

        meas_dict = {
            "d1": disp1,
            "d2": disp2,
            "d3": disp3,
            "c1": contrast1,
            "c2": contrast2,
            "c3": contrast3,
            "b1": baseline1,
            "b2": baseline2,
            "b3": baseline3,
            "w1": warn1,
            "w2": warn2,
            "w3": warn3,
            "m1": mixc1,
            "m2": mixc2,
            "m3": mixc3,
        }

        return meas_dict

    def get_error_id(self, err_number):
        """
        Return error identifier corresponding to <err_number>
        """
        return self.dev.system_service.errorNumberToString(0, err_number)

    def get_error_str(self, err_number):
        """
        Return error description corresponding to <err_number>
        """
        return self.dev.system_service.errorNumberToString(1, err_number)

    def get_error_hint(self, err_number):
        """
        Return error hint corresponding to <err_number>
        """
        return self.dev.system_service.errorNumberToRecommendation(0, err_number)

    def get_abs_positions(self):
        """
        Return a tuple of the 3 absolutes positions.
        """
        _, abs1, abs2, abs3 = self.dev.displacement.getAbsolutePositions()
        return (abs1, abs2, abs3)

    def get_measurement_string(self):
        """
        Format results
        * disp, contrast, baseline : taken from get_measurements()
        * abspos: from get_abs_positions()
        """
        tables = list()
        tables.append(("AXES:  ", "axis1", "axis2", "axis3"))

        meas_dict = self.get_measurements()

        mode = self.mode
        if mode == MODE.MEASURE:
            (abs1, abs2, abs3) = self.get_abs_positions()

            tables.append(("ABS POS: ", f"{abs1:,} pm", f"{abs2:,} pm", f"{abs3:,} pm"))
            tables.append(
                ("DISPLACEMENTS: ", meas_dict["d1"], meas_dict["d2"], meas_dict["d3"])
            )
            tables.append(
                ("CONTRAST:", meas_dict["c1"], meas_dict["c2"], meas_dict["c3"])
            )
            tables.append(
                ("BASELINE:", meas_dict["b1"], meas_dict["b2"], meas_dict["b3"])
            )

        elif mode == MODE.ALIGN:
            tables.append(
                ("CONTRAST:", meas_dict["c1"], meas_dict["c2"], meas_dict["c3"])
            )
            tables.append(
                ("BASELINE:", meas_dict["b1"], meas_dict["b2"], meas_dict["b3"])
            )
        elif mode == MODE.IDLE:
            return "Idle mode"
        else:
            return f"\nBad mode : {mode} \n\n"

        meas_str = tabulate(tables)

        # Add warnings and hints if needed.
        for (wa_idx, wa) in enumerate(["w1", "w2", "w3"]):
            warn = meas_dict[wa]
            if warn != 0 and not np.isnan(warn):
                warn_err_id = self.get_error_id(warn)
                warn_err_str = self.get_error_str(warn)
                warn_hint = "\n       ".join(
                    textwrap.wrap(self.get_error_hint(warn), 80)
                )
                meas_str += f"\nAXIS{wa_idx+1} WARNING: {warn_err_id} \n{warn_err_str} \nHINT: {warn_hint}\n"

        return meas_str

    @autocomplete_property
    def counters(self):
        """
        Return a counter namespace containing all counters.
        """
        return self._sampling_counter_controller.counters

    def __info__(self):
        info_str = f"{self.dev.system.getDeviceType()} : {self.name}\n"
        info_str += f"host: {self.host}\n"
        info_str += f"Mode: {MODE_TO_STR[self.mode]}\n"
        # init_mode = self.dev.system.getInitMode()
        # init_mode_str = INIT_MODES[init_mode]
        # info_str += f"Init Mode: {init_mode} : {init_mode_str}\n"

        info_str += (
            f"Firmware Version: {self.dev.system_service.getFirmwareVersion()}\n"
        )
        info_str += f"IP address: {self.dev.network.getRealIpAddress()}\n"
        info_str += f"MAC address: {self.dev.system_service.getMacAddress()}\n"

        info_str += self.get_measurement_string()

        return info_str

    def _switch_to_mode(self, target_mode):
        """
        Switch controller to <target_mode> mode.
        Not to be used in operation.
        TODO: expert mode only ?
        """
        current_mode = self.mode

        if current_mode == target_mode:
            print(f"{self.name} is already in {MODE_TO_STR[target_mode]} mode.")
            return

        _t0 = time.time()
        if current_mode == MODE.MEASURE:
            print("stopping Measurement mode")
            self.dev.system.stopMeasurement()
        elif current_mode == MODE.ALIGN:
            print("stopping Alignement mode")
            self.dev.system.stopOpticsAlignment()
        elif current_mode != MODE.IDLE:
            print(f"Bad mode: {current_mode}  (wait or try stop() command ?)")
            return

        time.sleep(0.1)
        print("Waiting for idle mode...")
        while self.mode != MODE.IDLE:
            print(".", end="", flush=True)
            if (time.time() - _t0) > 5:
                raise RuntimeError(
                    f"Error: {current_mode} not properly stopped after 5 seconds...(current mode is:{self.mode})"
                )
            time.sleep(0.5)
        print("")

        assert self.mode == MODE.IDLE
        print("Now in idle mode.")

        starting_duration = STARTING_DURATION[target_mode]
        _t0 = time.time()
        print(f"Starting {target_mode} (~{starting_duration}s) ...")

        if target_mode == MODE.ALIGN:
            self.dev.system.startOpticsAlignment()
        elif target_mode == MODE.MEASURE:
            self.dev.system.startMeasurement()
        else:
            print(f"bad mode : {target_mode}")

        time.sleep(1)

        mode = self.mode
        while mode != target_mode:
            print(".", end="", flush=True)
            mode = self.mode
            if (time.time() - _t0) > (starting_duration + 5):
                raise RuntimeError(
                    f"Error: {target_mode} not yet started after {starting_duration + 5} seconds...(current mode is:{mode})"
                )
            time.sleep(1)

        print(f"{self.mode} started in {int(time.time()-_t0)} seconds")

    def _set_optic_alignement_mode(self):
        """
        Switch controller to alignment mode.
        Not to be used in operation.
        TODO: expert mode only ?
        """
        self._switch_to_mode(MODE.ALIGN)

    def _set_measurement_mode(self):
        """
        Switch controller to measurement mode.
        """
        self._switch_to_mode(MODE.MEASURE)


# Bug in case of ctrl-C after a loop:
"""
ids IDS3010
    Mode: measurement running
    Firmware Version: 1.7.0
    IP address: 172.24.165.249
    MAC address: 70:B3:D5:D7:60:D8
---------------  ------------------  ------------------  ------------------
AXES:            axis1               axis2               axis3
ABS POS:         317,284,613,566 pm  317,246,920,930 pm  317,563,277,534 pm
DISPLACEMENTS:   -300859             -267143             -372503
CONTRAST:        840                 773                 0
BASELINE:        0                   0                   0
---------------  ------------------  ------------------  ------------------
response= {'jsonrpc': '2.0', 'id': 706, 'result': [0, 317284613292, 317246920666, 317563277236]}
ids IDS3010
    Mode: measurement running
    Firmware Version: 1.7.0
    IP address: 172.24.165.249
    MAC address: 70:B3:D5:D7:60:D8
---------------  ------------------  ------------------  ------------------
AXES:            axis1               axis2               axis3
ABS POS:         317,284,613,292 pm  317,246,920,666 pm  317,563,277,236 pm
DISPLACEMENTS:   -301133             -267407             -372801
CONTRAST:        840                 773                 0
BASELINE:        0                   0                   0
---------------  ------------------  ------------------  ------------------
response= {'jsonrpc': '2.0', 'id': 715, 'result': [0, 317284613623, 317246920996, 317563277517]}
ids IDS3010
    Mode: measurement running
    Firmware Version: 1.7.0
    IP address: 172.24.165.249
    MAC address: 70:B3:D5:D7:60:D8
---------------  ------------------  ------------------  ------------------
AXES:            axis1               axis2               axis3
ABS POS:         317,284,613,623 pm  317,246,920,996 pm  317,563,277,517 pm
DISPLACEMENTS:   -301288             -267480             -372918
CONTRAST:        840                 773                 0
BASELINE:        0                   0                   0
---------------  ------------------  ------------------  ------------------
response= {'jsonrpc': '2.0', 'id': 724, 'result': [0, 317284613897, 317246921285, 317563277754]}
!!! === KeyboardInterrupt:  === !!! ( for more details type cmd 'last_error' )

CYRIL [1]: ids
response= {'jsonrpc': '2.0', 'id': 729, 'result': [0, '70:B3:D5:D7:60:D8']}
!!! === IndexError: list index out of range === !!! ( for more details type cmd 'last_error' )

CYRIL [2]: ids
response= {'jsonrpc': '2.0', 'id': 734, 'result': [0, '70:B3:D5:D7:60:D8']}
!!! === IndexError: list index out of range === !!! ( for more details type cmd 'last_error' )

CYRIL [3]: ids
response= {'jsonrpc': '2.0', 'id': 739, 'result': [0, '70:B3:D5:D7:60:D8']}
!!! === IndexError: list index out of range === !!! ( for more details type cmd 'last_error' )

CYRIL [4]: ids
!!! === AttoException: JSON error in {'code': -32700, 'message': 'Parse Error', 'data': 'SyntaxError: Unexpected token { in JSON at position 91'} === !!! ( for more details type cmd 'last_error' )

CYRIL [5]: ids
response= {'jsonrpc': '2.0', 'id': 747, 'result': [0, 317284613794, 317246921223, 317563278145]}
  Out [5]: ids IDS3010
               Mode: measurement running
               Firmware Version: 1.7.0
               IP address: 172.24.165.249
               MAC address: 70:B3:D5:D7:60:D8
           ---------------  ------------------  ------------------  ------------------
           AXES:            axis1               axis2               axis3
           ABS POS:         317,284,613,794 pm  317,246,921,223 pm  317,563,278,145 pm
           DISPLACEMENTS:   -300631             -266850             -371892
           CONTRAST:        840                 773                 0
           BASELINE:        0                   0                   0
           ---------------  ------------------  ------------------  ------------------

"""
