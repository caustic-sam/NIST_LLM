# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import functools
import enum
import time

from bliss import global_map
from bliss.comm.util import get_comm
from bliss.common.protocols import CounterContainer
from bliss.common.utils import autocomplete_property
from bliss.common.logtools import log_debug, log_error
from bliss.common.counter import SamplingCounter
from bliss.controllers.counter import SamplingCounterController


def lazy_init(func):
    @functools.wraps(func)
    def func_wrapper(self, *args, **kwargs):
        if self.comm is None:
            self._initialize()
        return func(self, *args, **kwargs)

    return func_wrapper


@enum.unique
class EU_CODES(enum.IntEnum):
    """Engineering Units"""

    FS = 0
    SLPM = 1
    SLPH = 2
    SCCM = 3
    SCCH = 4
    SCFM = 5
    SCFH = 6
    SCMM = 7
    SCMH = 8
    LBPH = 9
    LBPM = 10
    GrPH = 11
    GrPM = 12


@enum.unique
class CR_CODES(enum.IntEnum):
    """Control reference"""

    INTERNAL = 0
    EXTERNAL = 1
    BATCH = 2
    TIMER = 3
    RATIO = 4


@enum.unique
class VM_CODES(enum.IntEnum):
    """Valve mode"""

    CLOSE = 0
    AUTO = 1
    OPEN = 2


class AalborgGFCDevice:

    """Aalborg GFC Mass Flow Controller

    https://www.aalborg.com/images/file_to_download/A_SDPROC%20Manual%20TD9704M%20RevF.pdf

    """

    WEOL = "\r"
    REOL = "\r\n"

    CMD2ARG = {
        # GENERAL CONTROL CMDS
        "FF": {"arg1": [1, 4], "arg2": [0.0, 99999.0]},  # unit = SLPM (l/min)
        "EU": {"arg1": [1, 4], "arg2": [0, 12]},  # unit = INDEX
        "RF": {"arg1": [1, 4], "arg2": [0, 4]},  # unit = INDEX
        "VM": {"arg1": [1, 4], "arg2": [0, 2]},  # unit = INDEX
        "SP": {"arg1": [1, 4], "arg2": [0.0, 105.0]},  # unit = %FS
        "DW": {"arg1": [1, 4], "arg2": [0.0, 999.999]},  # unit = g/l
        "DR": {"arg1": [1, 4]},  # unit = g/l
        "SD": {},  # unit = %FS
        # GENERAL STATUS CMDS
        "SCF": {},
        "SCS": {},
    }

    def __init__(self, config):
        self.config = config
        self.comm = None
        self.__needs_update_fs_eu = True
        self.__needs_update_ref_vmode_sp = True

    def __del__(self):
        self.__close__()

    def __close__(self):
        self._close_com()

    def _close_com(self):
        if self.comm is not None:
            self.comm.close()
            self.comm = None

    def _init_com(self):
        """Initialize communication or reset if already connected
        RS232 settings: 9600 baud, 8 bits, no parity, 2 stop bits
        """
        self._close_com()
        self.comm = get_comm(
            self.config, baudrate=9600, bytesize=8, stopbits=2, timeout=3, eol=self.REOL
        )

    def _initialize(self):
        """Initialize/reset communication layer and synchronize with hardware"""
        self._init_com()

        self._k_factor = self.config.get("k_factor", [1, 1, 1, 1])

        self._full_scale_flow = None
        self._engineering_unit_index = None
        self._reference_index = None
        self._valve_mode = None
        self._setpoints = None

        self.__needs_update_fs_eu = True
        self.__needs_update_ref_vmode_sp = True

        # === READ AND APPLY CONFIG ========================
        full_scale_flow = self.config.get("full_scale_flow")  # expected as l/min (SLPM)
        if full_scale_flow is not None:
            for i in range(4):
                self.set_full_scale_flow(i + 1, full_scale_flow[i])

        eng_units_index = self.config.get(
            "eng_units_index", [1, 1, 1, 1]
        )  # use SLPM as default units
        if eng_units_index is not None:
            for i in range(4):
                self.set_engineering_units(i + 1, eng_units_index[i])

        ctrl_ref_index = self.config.get(
            "ctrl_ref_index", [0, 0, 0, 0]
        )  # use internal mode as default
        if ctrl_ref_index is not None:
            for i in range(4):
                self.set_control_reference(i + 1, ctrl_ref_index[i])

        valve_mode_index = self.config.get(
            "valve_mode_index"
        )  # , [1, 1, 1, 1])  # use auto mode as default or keep actual state?
        if valve_mode_index is not None:
            for i in range(4):
                self.set_valve_mode(i + 1, valve_mode_index[i])

        gas_density = self.config.get("gas_density")  # expected as g/l
        if gas_density is not None:
            for i in range(4):
                self.set_gas_density(i + 1, gas_density[i])

    @property
    def k_factor(self):
        return self._k_factor

    @property
    def full_scale_flow(self):
        """Get full scale flow values for all channels in l/min"""
        if self.__needs_update_fs_eu:
            self.update_fs_eu()
        return self._full_scale_flow

    @property
    def engineering_unit_index(self):
        if self.__needs_update_fs_eu:
            self.update_fs_eu()
        return self._engineering_unit_index

    @property
    def reference_index(self):
        if self.__needs_update_ref_vmode_sp:
            self.update_ref_vmode_sp()
        return self._reference_index

    @property
    def valve_mode(self):
        if self.__needs_update_ref_vmode_sp:
            self.update_ref_vmode_sp()
        return self._valve_mode

    @property
    def setpoints(self):
        """Get setpoint values for all channels in %FS"""
        if self.__needs_update_ref_vmode_sp:
            self.update_ref_vmode_sp()
        return self._setpoints

    @property
    def gas_density(self):
        return [self.get_gas_density(i + 1) for i in range(4)]

    @lazy_init
    def send_cmd(self, cmd, arg1=None, arg2=None, arg3=None, arg4=None):

        # --- check command validity
        if self.CMD2ARG.get(cmd) is None:
            raise ValueError(f"unknown command '{cmd}'")

        # --- filter and check arguments validity
        args = [cmd]
        for idx, arg in enumerate([arg1, arg2, arg3, arg4]):
            key = f"arg{idx+1}"
            if arg is not None:
                if self.CMD2ARG[cmd].get(key) is None:
                    raise ValueError(
                        f"command '{cmd}' does not expect argument '{arg}'"
                    )

                rng = self.CMD2ARG[cmd][key]
                if isinstance(rng, list) and len(rng) == 2:
                    if arg < rng[0] or arg > rng[1]:
                        raise ValueError(f"value must be in range {rng}")

                args.append(arg)

        # --- build the full command
        msg = f"{' '.join(str(arg) for arg in args)}{self.WEOL}"

        for retry in range(4):
            try:
                ans = self.comm.write_readline(msg.encode())
                ans = ans.decode()
            except UnicodeDecodeError as e:
                log_error(
                    self,
                    f"send_cmd (retry={retry}): {msg} => error: {e} with answer {ans}",
                )
                if retry > 2:
                    raise e
                time.sleep(0.1)
            else:
                break

        log_debug(self, f"send_cmd: {msg} => recv: {ans}")

        if "ERROR" in ans:
            raise RuntimeError(f"Error in command '{msg}' with response '{ans}'")

        return ans

    def set_full_scale_flow(self, channel, value):
        """Set full-scale flow in l/min for a given channel"""
        self.__needs_update_fs_eu = True
        return self.send_cmd("FF", arg1=channel, arg2=float(value))

    def set_engineering_units(self, channel, value):
        """Set flow engineering units (int or str) for a given channel"""

        if value not in list(
            EU_CODES
        ):  # i.e value is not an integer or not in possible values
            try:
                value = int(EU_CODES[value])  # check if value is a valid string
            except KeyError:
                raise ValueError(
                    "Invalid value '%s', should be in %s"
                    % (value, list(EU_CODES.__members__.keys()))
                )
        self.__needs_update_fs_eu = True
        return self.send_cmd("EU", arg1=channel, arg2=value)

    def set_control_reference(self, channel, value):
        """Set control reference (int or str) for a given channel"""

        if value not in list(
            CR_CODES
        ):  # i.e value is not an integer or not in possible values
            try:
                value = int(CR_CODES[value])  # check if value is a valid string
            except KeyError:
                raise ValueError(
                    "Invalid value '%s', should be in %s"
                    % (value, list(CR_CODES.__members__.keys()))
                )
        self.__needs_update_ref_vmode_sp = True
        return self.send_cmd("RF", arg1=channel, arg2=value)

    def set_valve_mode(self, channel, value):
        """Set valve mode (int or str) for a given channel"""

        if value not in list(
            VM_CODES
        ):  # i.e value is not an integer or not in possible values
            try:
                value = int(VM_CODES[value])  # check if value is a valid string
            except KeyError:
                raise ValueError(
                    "Invalid value '%s', should be in %s"
                    % (value, list(VM_CODES.__members__.keys()))
                )
        self.__needs_update_ref_vmode_sp = True
        return self.send_cmd("VM", arg1=channel, arg2=value)

    def set_setpoint(self, channel, value):
        """Set setpoint in %FS for a given channel"""

        # open valve if closed and sp != 0
        if self.valve_mode[channel - 1] == 0 and value != 0:
            self.set_valve_mode(channel, 1)

        self.__needs_update_ref_vmode_sp = True
        ans = self.send_cmd("SP", arg1=channel, arg2=value)

        # close valve if opened and sp == 0
        if value == 0 and self.valve_mode[channel - 1] != 0:
            self.set_valve_mode(channel, 0)

        return ans

    def set_gas_density(self, channel, value):
        """Set gas-density in g/l for a given channel"""
        return self.send_cmd("DW", arg1=channel, arg2=value)

    def get_gas_density(self, channel):
        """Get gas-density in g/l for a given channel"""
        return float(self.send_cmd("DR", arg1=channel).split()[1])

    def set_flow(self, channel, value):
        """Set flow setpoint in l/min for a given channel"""
        fs = self.unit2FS(channel, value)
        return self.set_setpoint(channel, fs)

    def get_flow(self):
        """Get flow setpoint values for all channels in l/min for a given channel"""
        return self.FS2unit(self.setpoints)

    def get_curr_flow(self, convert=True):
        """Get current flow for all channels in l/min if convert is True else %FS"""
        ans = self.send_cmd(
            "SD"
        )  # ex: '#1=   8.0%I  #2=   0.2%I  #3=   0.9%I  #4=   0.2%I  '
        flows = [float(x.strip()[:-2]) for x in ans.split()[1::2]]
        if convert:
            return self.FS2unit(flows)
        return flows

    def get_status_1(self):
        """General status command #1 returning for all channels:
        {Instrument model, TCP/IP flag, Full scale flow in SLPM, Current E.U}
        """
        return self.send_cmd(
            "SCF"
        )  # ex: 'SCF SDPROC4 0 50.00 50.00 2.000 50.00 1 1 1 1'

    def get_status_2(self):
        """General status command #2 returning for all channels:
        {Reference, Valve mode, Setpoint}
        """
        return self.send_cmd("SCS")  # ex: 'SCS 0 0 0 0 1 1 1 0 8.0 30.0 5.0 0.0'

    def update_fs_eu(self):
        """Read and update current values of the Full-scale-flow and Engineering-unit-index of all channels"""
        ans = self.send_cmd(
            "SCF"
        ).split()  # ex: 'SCF SDPROC4 0 50.00 50.00 2.000 50.00 1 1 1 1'
        self._full_scale_flow = [float(x) for x in ans[3:7]]
        self._engineering_unit_index = [int(x) for x in ans[7:11]]
        self.__needs_update_fs_eu = False

    def update_ref_vmode_sp(self):
        """Read and update current values of the Reference, Valve mode and Setpoint of all channels"""
        ans = self.send_cmd("SCS").split()  # ex: 'SCS 0 0 0 0 1 1 1 0 8.0 30.0 5.0 0.0'
        self._reference_index = [int(x) for x in ans[1:5]]
        self._valve_mode = [int(x) for x in ans[5:9]]
        self._setpoints = [float(x) for x in ans[9:13]]  # in %FS
        self.__needs_update_ref_vmode_sp = False

    def unit2FS(self, channel, flow):
        """Convert a flow value from l/min to %FS"""
        return (
            100
            * flow
            / (self.k_factor[channel - 1] * self.full_scale_flow[channel - 1])
        )

    def FS2unit(self, fsflows):
        """Convert flow list values (all-channels) from %FS to l/min"""
        return [
            fsflows[i] * self.k_factor[i] * self.full_scale_flow[i] / 100.0
            for i in range(len(fsflows))
        ]


class AalborgCounterController(SamplingCounterController):
    TAG2INDEX = {"flow_1": 0, "flow_2": 1, "flow_3": 2, "flow_4": 3}

    def __init__(self, name, ctrl):
        super().__init__(name, register_counters=False)
        self._ctrl = ctrl

    def read_all(self, *counters):
        flows = self._ctrl.get_curr_flow()
        return [flows[self.TAG2INDEX[cnt.name]] for cnt in counters]


class Aalborg(AalborgGFCDevice, CounterContainer):
    def __init__(self, config):
        """
        - class: Aalborg
          plugin: generic
          name: flowctl

          serial:
              url: ser2net://lid221:28000/dev/ttyRP12

          # Full-Scale Flow for 4 channels in l/min
          full_scale_flow: [50.0, 50.0, 2.0, 50.0]

          # K-factor for all 4 channels:
          # 1 means we use air or nitrogen, 1.454 for He<10L/min, 2.05 for He>10-50L/min??
          k_factor: [1, 1, 1, 1]

          # Gas density in g/l (= air/nitrogen)
          gas_density: [1.293, 0.1786, 0.1786, 0.1786]

        """

        AalborgGFCDevice.__init__(self, config)
        self._name = config["name"]
        self._scc = AalborgCounterController(self.name, self)

        for chan in [1, 2, 3, 4]:
            self._scc.create_counter(SamplingCounter, f"flow_{chan}", mode="SINGLE")

        global_map.register(self, parents_list=["controllers", "counters"])

    def __info__(self):
        from bliss.shell.formatters.table import IncrementalTable

        eul = list(EU_CODES)
        vml = list(VM_CODES)
        flow = self.get_curr_flow()
        sp = self.setpoints
        fs = self.full_scale_flow
        unit = [eul[idx].name for idx in self.engineering_unit_index]
        gd = self.gas_density
        vm = [vml[idx].name for idx in self.valve_mode]

        labels = [
            " channels ",
            " valve ",
            " density [g/l] ",
            " full scale ",
            " setpoint ",
            " curr flow ",
            " unit ",
        ]
        tab = IncrementalTable([labels], col_sep="|", flag="")
        tab.add_separator("-")
        for i in range(4):
            tab.add_line([i + 1, vm[i], gd[i], fs[i], sp[i], flow[i], unit[i]])

        tab.set_column_params(
            2, {"fpreci": ".2", "dtype": "f", "align": "^", "flag": ""}
        )
        tab.set_column_params(
            3, {"fpreci": ".2", "dtype": "f", "align": ">", "flag": ""}
        )
        tab.set_column_params(
            4, {"fpreci": ".2", "dtype": "f", "align": ">", "flag": ""}
        )
        tab.set_column_params(
            5, {"fpreci": ".2", "dtype": "f", "align": ">", "flag": ""}
        )

        tab.resize(8, 16)

        header = "\n                  ***  Aalborg GFC Mass Flow Controller ***                     \n\n"

        return header + str(tab)

    @property
    def name(self):
        return self._name

    @autocomplete_property
    def counters(self):
        return self._scc.counters
