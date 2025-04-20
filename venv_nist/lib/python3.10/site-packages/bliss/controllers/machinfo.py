# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.


"""
BLISS controller to access to accelerator information.

MachInfo class provides mechanisms to deal with information obtained from the
ESRF accelerator:

* properties to get some machine available information
    - sr_mode:  operation mode
    - automatic_mode: automatic FE mode activation
    - tango_uri: address of the Front-End Tango device server
* counters to access to accelerator information:
    - current
    - lifetime
    - sbcurr
    - refill
    - all_information
* helper functions to implement refill pretection in a sequence
    - iter_wait_for_refill
    - check_for_refill
    - WaitForRefillPreset

"""


import enum
import gevent
from tabulate import tabulate, SEPARATING_LINE
import warnings

from bliss import global_map
from bliss.config.beacon_object import BeaconObject
from bliss.common.scans import DEFAULT_CHAIN
from bliss.common.user_status_info import status_message
from bliss.common.utils import autocomplete_property
from bliss.common import timedisplay
from bliss.controllers.counter import counter_namespace

from bliss.scanning.chain import ChainPreset, ChainIterationPreset
from bliss.common import tango
from bliss.common.protocols import HasMetadataForDataset, CounterContainer
from bliss.scanning.scan_meta import HasMetadataForScan
from bliss.controllers.tango_attr_as_counter import (
    TangoCounterController,
    TangoAttrCounter,
)


class MachInfo(
    CounterContainer, BeaconObject, HasMetadataForScan, HasMetadataForDataset
):
    """Access to accelerator information.

    - SR_Current
    - SR_Lifetime
    - SR_Single_Bunch_Current
    - SR_Refill_Countdown
    """

    class SRMODE(enum.Enum):
        Unknown = -1
        USM = 1
        MDT = 2
        Shutdown = 3
        SafetyTest = 4
        IdTest = 5

    class INJECTION_MODE(enum.Enum):
        Unknown = -1
        NoModeValidated = 0
        Injection = 1
        BeamDelivery = 2

    name = BeaconObject.config_getter("name")

    # Time added to checktime or to count_time.
    extra_checktime = BeaconObject.property_setting("extra_checktime", default=0.0)

    # Time to wait after the beam is back (for beam stabilization or reheating).
    waittime = BeaconObject.property_setting("waittime", default=0)

    COUNTERS = (
        ("current", "SR_Current"),
        ("lifetime", "SR_Lifetime"),
        ("sbcurr", "SR_Single_Bunch_Current"),
        ("refill", "SR_Refill_Countdown"),
    )
    KEY_NAME = "MACHINE"

    def __init__(self, name, config):
        super().__init__(config, share_hardware=False)
        global_map.register(self, tag=name)
        self.tango_uri = config["uri"]
        self.__counters = []
        self.__machinfo_dev = None

        try:
            controller = TangoCounterController(
                name, self.proxy, global_map_register=False
            )
            for cnt_name, attr_name in self.COUNTERS:
                counter_config = config.clone()
                counter_config["attr_name"] = attr_name
                counter_config["mode"] = "SINGLE"
                cnt = TangoAttrCounter(cnt_name, counter_config, controller)

                # NB: "cnt.allow_failure==True"
                #     means "the scan will crash if reading of cnt fails"
                cnt.allow_failure = False
                self.__counters.append(cnt)
        except Exception:
            global_map.unregister(self)  # issue 3029
            raise

        self.__counters_groups = {}
        self.__check = False
        default_counters = config.get("default_counters", [])
        if default_counters:
            # check if allowed (ie: name is in self.COUNTERS)
            allowed_counters = set(c[0] for c in self.COUNTERS)
            not_allowed = [
                name for name in default_counters if name not in allowed_counters
            ]
            if not_allowed:
                raise AttributeError(
                    f"Default counters must be part of {allowed_counters},"
                    f"{not_allowed} are not allowed"
                )
            self._counter_grp = {
                "default": counter_namespace(
                    [
                        counter
                        for counter in self.__counters
                        if counter.name in default_counters
                    ]
                )
            }
        else:
            self._counter_grp = {}

        self.initialize()

    @property
    def proxy(self):
        """
        Create connection to machine device server if not already done.
        """
        if self.__machinfo_dev:
            return self.__machinfo_dev

        # If FE does not exist: raise an exception.
        # Better crash than run with a defective device.
        machinfo_dev = tango.DeviceProxy(self.tango_uri)

        # At creation of the connection:
        # Ping the server to avoid a re-connection on set_timeout_millis().
        # Reduce timeout to avoid to wait too much in case of error.
        # No exception catching: better crash than run with a defective device.
        machinfo_dev.ping()
        machinfo_dev.set_source(tango.DevSource.CACHE_DEV)
        machinfo_dev.set_timeout_millis(200)
        self.__machinfo_dev = machinfo_dev
        return machinfo_dev

    @autocomplete_property
    @BeaconObject.lazy_init
    def counters(self):
        return counter_namespace(self.__counters)

    @property
    @BeaconObject.lazy_init
    def counter_groups(self):
        return counter_namespace(self._counter_grp)

    @property
    def check(self):
        """
        Install a preset for all common scans to pause them when a refill occurs.
        """
        return self.__check

    @check.setter
    def check(self, activate):
        """
        <activate> (boolean) : activate or remove the WaitForRefillPreset preset in default chain.
        """
        if activate:
            preset = WaitForRefillPreset(self, waittime=self.waittime)
            DEFAULT_CHAIN.add_preset(preset, name=self.KEY_NAME)
            self.__check = True
            print("Activating Wait For Refill on scans")
        else:
            DEFAULT_CHAIN.remove_preset(name=self.KEY_NAME)
            self.__check = False
            print("Removing Wait For Refill on scans")

    def dataset_metadata(self):
        attributes = ["SR_Mode", "SR_Current"]
        attributes = {
            attr_name: value
            for attr_name, value in zip(attributes, self._read_attributes(attributes))
        }
        return {
            "mode": self.SRMODE(attributes["SR_Mode"]).name,
            "current": attributes["SR_Current"],
        }

    @property
    def scan_metadata_name(self):
        return "machine"

    def scan_metadata(self):
        attributes = [
            "SR_Mode",
            "SR_Filling_Mode",
            "SR_Single_Bunch_Current",
            "SR_Current",
            "Automatic_Mode",
            "FE_State",
            "SR_Refill_Countdown",
            "SR_Operator_Mesg",
        ]
        attributes = {
            attr_name: value
            for attr_name, value in zip(attributes, self._read_attributes(attributes))
        }
        # Standard:
        meta_dict = {
            "@NX_class": "NXsource",
            "name": "ESRF",
            "type": "Synchrotron",
            "mode": self.SRMODE(attributes["SR_Mode"]).name,
            "current": attributes["SR_Current"],
            "current@units": "mA",
        }
        # Non-standard:
        if attributes["SR_Filling_Mode"] == "1 bunch":
            meta_dict["single_bunch_current"] = attributes["SR_Single_Bunch_Current"]
            meta_dict["single_bunch_current@units"] = "mA"
        meta_dict["filling_mode"] = attributes["SR_Filling_Mode"]
        meta_dict["automatic_mode"] = attributes["Automatic_Mode"]
        meta_dict["front_end"] = attributes["FE_State"]
        meta_dict["refill_countdown"] = attributes["SR_Refill_Countdown"]
        meta_dict["refill_countdown@units"] = "s"
        meta_dict["message"] = attributes["SR_Operator_Mesg"]
        return meta_dict

    @BeaconObject.property()
    def metadata(self):
        """
        Insert machine info metadata's for any scans
        """
        pass

    @metadata.setter
    def metadata(self, flag):
        if flag:
            warnings.warn("Use 'MachInfo.enable_scan_metadata' instead", FutureWarning)
            self.enable_scan_metadata()
        else:
            warnings.warn("Use 'MachInfo.disable_scan_metadata' instead", FutureWarning)
            self.disable_scan_metadata()

    def iter_wait_for_refill(self, checktime, waittime=0.0, polling_time=1.0):
        r"""
        Helper for waiting the machine refill.

        It will yield two states "WAIT_INJECTION" and "WAITING_AFTER_BEAM_IS_BACK"
        until the machine refill is finished.

        Simple usage will be:

        .. code-block:: python

            from bliss.common.user_status_info import status_message
            with status_message() as update:
                for status in iter_wait_for_refill(my_check_time, waittime=1., polling_time=1.):
                    if status == "WAIT_INJECTION":
                        update("Scan is paused, waiting injection")
                    else:
                        update("Scan will restart in some seconds...")
        """
        ok = self.check_for_refill(checktime)
        while not ok:
            yield "WAIT_INJECTION"
            gevent.sleep(polling_time)
            ok = self.check_for_refill(checktime)
            if ok:
                yield "WAITING_AFTER_BEAM_IS_BACK"
                gevent.sleep(waittime)
                ok = self.check_for_refill(checktime)

    def check_for_refill(self, checktime):
        """
        Return True if the <checktime> is smaller than the `refill countdown`.
        ie: return True if a task of length <checktime> can be performed before
        the refill.
        """
        attr_to_read = ("SR_Mode", "SR_Refill_Countdown")

        mode, countdown = None, 99999

        # Repeat the reading 4 times.
        # timeout is already set to 200ms to reduce wait time.
        for i in range(4):
            try:
                mode, countdown = self._read_attributes(attr_to_read)
                break
            except Exception:
                print(
                    f"Timeout ({self.proxy.get_timeout_millis()} ms) reading machine server {self.tango_uri} -> retrying"
                )
                gevent.sleep(0.5)

        if mode is None:
            mode, countdown = self._read_attributes(attr_to_read)

        if mode != self.SRMODE.USM.value:
            return True
        return countdown > checktime

    def __info__(self):
        str_info = f"MACHINE INFORMATION   ( {self.tango_uri} )\n\n"
        attributes = (
            "SR_Mode",
            "SR_Current",
            "SR_Lifetime",
            "SR_Single_Bunch_Current",
            "Automatic_Mode",
            "SR_Filling_Mode",
            "SR_Refill_Countdown",
            "SR_Operator_Mesg",
        )
        tables = []

        (
            sr_mode,
            sr_curr,
            ltime,
            sb_curr,
            fe_auto,
            sr_filling_mode,
            refill_time,
            op_message,
        ) = self._read_attributes(attributes)

        # FE DS url
        tables.append(("Device Server:", f"{self.proxy.name()}"))

        # SR_Mode: MDT, USM ...
        tables.append(("SR Mode:", f"{self.SRMODE(sr_mode).name}"))

        # Injection Mode:
        tables.append(("Injection Mode:", self.injection_mode_as_string))

        if self.sr_mode not in [3]:
            # not in shutdown mode

            # SR_Current is in mA.
            tables.append(("Current:", f"{sr_curr:3.2f} mA"))

            # SR_Lifetime is in seconds with too much decimals.
            ltime = int(ltime)
            tables.append(
                ("Lifetime:", f"{ltime} s = {timedisplay.duration_format(ltime)}")
            )

            # SR_Refill_Countdown is in seconds.
            refill_time
            tables.append(
                (
                    "Refill CountDown:",
                    f"{int(refill_time)} s = {timedisplay.duration_format(refill_time)}",
                )
            )

            # SR_Filling_Mode value: '7/8 multibunch', '1 bunch'
            tables.append(("Filling Mode:", sr_filling_mode))

            if sr_filling_mode == "1 bunch":
                # SR_Single_Bunch_Current is in mA.
                tables.append(("Single Bunch Cur:", sb_curr))

            tables.append(("AutoMode:", fe_auto))
        tables.append(SEPARATING_LINE)
        tables.append(("Operator Message", op_message))
        tables.append(SEPARATING_LINE)
        tables.append(("Refill check ", self.check))
        tables.append(
            (
                "Refill extra_checktime ",
                f"{self.extra_checktime} s (added to count time or checktime)",
            )
        )
        tables.append(
            (
                "Refill waittime ",
                f"{self.waittime} s (time waited after the come-back of the beam)",
            )
        )

        str_info = tabulate(tables)

        return str_info

    def _read_attributes(self, attr_to_read):
        """
        Read one or many attributes at once from machine Tango device server.
        """
        dev_attrs = self.proxy.read_attributes(attr_to_read)

        # Check error
        for attr in dev_attrs:
            error = attr.get_err_stack()
            if error:
                raise tango.DevFailed(*error)
        return (attr.value for attr in dev_attrs)

    @property
    def sr_mode_as_string(self):
        (mode,) = self._read_attributes(("SR_Mode",))
        return self.SRMODE(mode).name

    @property
    def sr_mode(self):
        (mode,) = self._read_attributes(("SR_Mode",))
        return mode

    @property
    def injection_mode_as_string(self):
        """
        Return injection mode ('Mode' in tango device)
        """
        (mode,) = self._read_attributes(("Mode",))
        return self.INJECTION_MODE(mode).name

    @property
    def injection_mode(self):
        (mode,) = self._read_attributes(("Mode",))
        return mode

    @property
    def automatic_mode(self):
        """
        Return the FE automatic mode status, True or False
        """
        (mode,) = self._read_attributes(("Automatic_Mode",))
        return mode

    @automatic_mode.setter
    def automatic_mode(self, newmode):
        """
        Set the automatic mode of the FE, True or False
        """
        if newmode is True:
            self.proxy.Automatic()
        else:
            self.proxy.Manual()

    @property
    def all_information(self):
        """
        Return most of all the machine information as a dictionary
        """
        attributes = (
            "FE_State",
            "SR_Mode",
            "Mode",
            "SR_Current",
            "SR_Lifetime",
            "SR_Single_Bunch_Current",
            "Automatic_Mode",
            "SR_Filling_Mode",
            "SR_Refill_Countdown",
            "SR_Operator_Mesg",
            "FE_Itlk_State",
            "PSS_Itlk_State",
            "EXP_Itlk_State",
            "HQPS_Itlk_State",
            "UHV_Valve_State",
        )
        attributes = {
            attr_name: value
            for attr_name, value in zip(attributes, self._read_attributes(attributes))
        }
        attributes["SR_Mode"] = self.SRMODE(attributes["SR_Mode"]).name
        attributes["Mode"] = self.INJECTION_MODE(attributes["Mode"]).name
        return attributes


class WaitForRefillPreset(ChainPreset):
    """
    WaitForRefillPreset is a preset to pause a scan during the refill.
    It is added to the default chain by setting `machinfo.check` property to True.

    If the **checktime** is greater than the time to refill.
    If **checktime** is set to None then we try to find **count_time**
    on the top master of the chain.

    Do not forget to initialize MachInfo object in session's setup.
    """

    class PresetIter(ChainIterationPreset):
        def __init__(self, machinfo, checktime, waittime, polling_time):
            self.machinfo = machinfo
            self.checktime = checktime
            self.waittime = waittime
            self.polling_time = polling_time

        def start(self):
            ok = self.machinfo.check_for_refill(self.checktime)
            with status_message() as p:
                while not ok:
                    p("Waiting for refill...")
                    gevent.sleep(self.polling_time)
                    ok = self.machinfo.check_for_refill(self.checktime)
                    if ok and self.waittime:
                        p("Waiting {self.waittime} after Beam is back")
                        gevent.sleep(self.waittime)
                        ok = self.machinfo.check_for_refill(self.checktime)

    def __init__(self, machinfo, checktime=None, waittime=None, polling_time=1.0):
        self.machinfo = machinfo
        self.__checktime = checktime
        self.waittime = waittime
        self.polling_time = polling_time

    def get_iterator(self, chain):
        if self.__checktime is None:
            # will look into the chain to find **count_time**
            # on the first softtimer.
            for soft_timer in chain.nodes_list:
                try:
                    count_time = soft_timer.count_time
                except AttributeError:
                    pass
                else:
                    checktime = count_time
                    if self.machinfo.extra_checktime is not None:
                        checktime += self.machinfo.extra_checktime
                    break
            else:
                raise RuntimeError(
                    "Couldn't guess the checktime because didn't "
                    "find any soft timer..."
                    "You need to set checktime for custom scans"
                )
        else:
            checktime = self.__checktime

        while True:
            yield self.PresetIter(
                self.machinfo, checktime, self.waittime, self.polling_time
            )
