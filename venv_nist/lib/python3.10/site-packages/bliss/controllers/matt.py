# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Monochromatic attenuators, controlled via wago - pneumatic actuators and ADC.

yml configuration example:

.. code-block::

    name: matt
    class: matt
    controller_ip: wcid29a
    nb_filter: 12
    att_type: 2
    control_module: "750-516"


The filters are controlled pneumatically via WAGO output modules.

The position is red from WAGO modules, in a different way, depending
on the mechanics (4 types - 0 to 3).

The input and output modules are next to each other, so there should
not be any other wago module between.

The different configurations are:

- type 0: one control bit to move a filter, two limit switch bits to
          read the position.
          To insert the filter in the beam, the control bit is set to 1, to
          extract it - to 0. The IN limit switch bit is 1 and the OUT
          limit switch bit is 0 when in or inverse when the filter is out of
          the beam. The even numbers (including 0) are the IN and the odd
          numbers are the OUT bits. The input modules have 4 channels each
          and if more than 4 filters, all the modules are consecutive. The
          output modules have 8 filters and if more than 4 filters, all the
          modules are consecutive.
- type 1: one control bit to move a filter, one limit switch bit to
          read the position.
          To insert the filter in the beam, the control bit is set to 1, to
          extract it - to 0. The limit switch bit is 1 when the filter is in
          the beam, 0 when out. The input and output modules have 4 channels.
- type 2: one control bit to move a filter, two limit switch bits to
          read the position.
          To insert the filter in the beam, the control bit is set to 1, to
          extract it - to 0. Bits 4 -7 are 1 when the filters 0-4 are in the
          beam and 0, when out of the beam. The first four channels of the
          input modules are not used.
          The input modules have 4 channels each, the output  modules - 8
          channels each. If more than 4 filters, the input and the output
          modules alternate.
- type 3: one control bit to move a filter, two limit switch bits to
          read the position.
          To insert the filter in the beam, the control bit is set to 1, to
          extract it - to 0. The odd numbers are 1 when the filter is in the
          beam and 0, when out of the beam. The even numbers are not used.
          The input modules have 4 channels each, the output  modules - 8
          channels each. If more than 4 filters, the input and the output
          modules alternate.

The 750-436 and 740-430 Digital Input modules have 8 channels, while
the 750-402 and 750-408 Digital Input module has 4 channels only.
"""

from gevent import Timeout, sleep
from bliss.controllers.wago.wago import WagoController, ModulesConfig
from bliss.comm.util import get_comm
from bliss.common.utils import wrap_methods
from bliss.common.logtools import log_debug
from bliss import global_map


class MattWagoMapping:
    """Create standard attenuators WAGO mapping"""

    def __init__(self, nb_filter, att_alternate, stat_m, ctrl_m):
        self.nb_filter = nb_filter
        # self.att_type = att_type
        self.att_alternate = att_alternate
        self.mapping = []
        self.generate_mapping(att_alternate, stat_m, ctrl_m)

    def __repr__(self):
        return "\n".join(self.mapping)

    def generate_mapping(self, att_alternate, stat_m, ctrl_m):
        """Generat the attenuators wago mapping.

        Args:
            att_alternate (int): alternate control/status cards.
            stat_m (str): Name of the status module(s)
            ctrl_m (str): Name of the control module(s).
        """
        nstat = 2
        nctrl = 1
        STATUS_MODULE = "%s" % stat_m + ",%s"
        CONTROL_MODULE = "%s" % ctrl_m + ",%s"
        n_mod = 1
        if stat_m in ("750-402", "750-408"):
            nstat = 1
            n_mod = nstat * 2

        if ctrl_m == "750-530":
            nctrl = 2

        STATUS = ["attstatus"] * nstat
        if nctrl == 2:
            CONTROL = ["attctrl,_"]
        else:
            CONTROL = ["attctrl"] * nctrl

        mapping = []
        nb_chan = self.nb_filter
        ch_ctrl = nb_chan // 4
        ch_stat = ch_ctrl * n_mod
        ch = nb_chan % 4

        if nb_chan > 4:
            if att_alternate is True:
                for i in range(ch_ctrl):
                    mapping += [CONTROL_MODULE % ",".join(CONTROL * 4)]
                    mapping += [STATUS_MODULE % ",".join(STATUS * 4)]
                if ch > 0:
                    mapping += [
                        CONTROL_MODULE
                        % ",".join(CONTROL * (ch) + ["_"] * (4 * nctrl - ch * nctrl))
                    ]
                    mapping += [
                        STATUS_MODULE
                        % ",".join(STATUS * ch + ["_"] * (4 * nstat - ch * nstat))
                    ]
            else:
                for i in range(ch_ctrl):
                    mapping += [CONTROL_MODULE % ",".join(CONTROL * 4)]
                if ch > 0:
                    mapping += [
                        CONTROL_MODULE
                        % ",".join(CONTROL * (ch) + ["_"] * (4 * nctrl - ch * nctrl))
                    ]
                for i in range(ch_stat):
                    mapping += [STATUS_MODULE % ",".join(STATUS * 4)]
                if ch > 0:
                    mapping += [
                        STATUS_MODULE
                        % ",".join(STATUS * ch + ["_"] * (4 * nstat - ch * nstat))
                    ]
        else:
            mapping += [
                CONTROL_MODULE % ",".join(CONTROL * nb_chan + ["_"] * (4 - nb_chan))
            ]
            if ch > 0:
                mapping += [
                    STATUS_MODULE
                    % ",".join(STATUS * ch + ["_"] * (4 * nstat - ch * nstat))
                ]
            else:
                mapping += [STATUS_MODULE % ",".join(STATUS * nb_chan)]

        self.mapping = mapping


class MattControl:
    """Control the monochriomatic attenuators"""

    def __init__(
        self,
        wago_ip,
        nb_filter,
        att_type=0,
        att_alternate=False,
        stat_m="750-436",
        ctrl_m="750-530",
    ):
        self.__wago = None
        self.wago_ip = wago_ip
        self.nb_filter = nb_filter
        self.att_type = att_type
        self.att_alternate = att_alternate
        self.stat_m = stat_m
        self.ctrl_m = ctrl_m
        self.exec_timeout = 5
        self.mapping = []
        global_map.register(self)

    @property
    def wago(self):
        """Get the WAGO object.

        Raises:
            RuntimeError: Not connected to a Wago
        """
        try:
            return self.__wago
        except AttributeError:
            raise RuntimeError("Not connected to a Wago")

    def connect(self):
        """Connect to a WAGO. Generate the mapping."""
        log_debug(self, "In connect()")
        self.mapping = MattWagoMapping(
            self.nb_filter, self.att_alternate, self.stat_m, self.ctrl_m
        )
        modules_config = ModulesConfig(str(self.mapping), ignore_missing=True)

        log_debug(self, "Trying to connect to Wago")
        conf = {"modbustcp": {"url": self.wago_ip}}
        comm = get_comm(conf)

        wago = WagoController(comm, modules_config)
        wago.connect()
        self.__wago = wago

        global_map.register(self, children_list=[self.wago])

    def exit(self):
        """Close the connectionn to the WAGO."""
        log_debug(self, "In exit()")
        self.wago.close()

    def pos_read(self) -> int:
        """Read the position of the filters.

        Returns:
            The value representing the addition of the active bits.
        """
        log_debug(self, "In pos_read()")
        ret = 0
        nstat = 2

        stat = self.wago.get("attstatus")

        del stat[(self.nb_filter * nstat) :]

        if self.att_type == 1:
            ret = self.read_1posbit(stat)
        elif self.att_type == 2:
            ret = self.read_2posbit_alternate(stat)
        elif self.att_type == 3:
            ret = self.read_2posbit_odd(stat)
        else:
            ret = self.read_2posbit(stat)
        return ret

    def read_1posbit(self, stat):
        """Read the value for 1 position bit configuration.

        Returns:
            (int): The value, representing the addition of the active bits.
        """
        log_debug(self, "In read_1posbit(%s)", stat)
        ret = 0
        for i in range(self.nb_filter):
            ret += stat[i] << i
        return ret

    def read_2posbit_odd(self, stat):
        """Read the value for 2 position odd bits configuration.

        Returns:
            (int): The value, representing the addition of the active bits.
        """
        log_debug(self, "In read_2posbit_odd(%s)", stat)
        ret = 0
        nstat = 2
        for i in range(self.nb_filter):
            if stat[nstat * i + 1]:
                pos = 1 << i
            else:
                pos = 0
            ret += pos
        return ret

    def read_2posbit(self, stat):
        """Read the value for 2 position bits configuration.

        Returns:
            (int): The value, representing the addition of the active bits.
        """
        log_debug(self, "In read_2posbit(%s)", stat)
        ret = 0
        nstat = 2
        for i in range(self.nb_filter):
            if not stat[nstat * i] and stat[nstat * i + 1]:
                pos = 0
            elif stat[nstat * i] and not stat[nstat * i + 1]:
                pos = 1 << i
            else:
                pos = 1 << (i + self.nb_filter)
            ret += pos
        return ret

    def read_2posbit_alternate(self, stat):
        """Read the value for 2 position bits, alternatingn status modules
        configuration.

        Returns:
            (int): The value, representing the addition of the active bits.
        """
        log_debug(self, "In read_2posbit_alternate(%s)", stat)
        ret = 0
        nstat = 2
        for i in range(self.nb_filter):
            gidx = i % 4
            groupno = int(i / 4)
            idx = groupno * 4 * nstat + 4 + gidx
            ret += stat[idx] << i
        return ret

    def pos_write(self, value):
        """Write a position value to the control module(s).

        Args:
            (int): The value, representing the addition of the active bits.
        """
        log_debug(self, "In pos_write(%s)", value)
        valarr = [False] * self.nb_filter

        for i in range(self.nb_filter):
            if value & (1 << i) > 0:
                valarr[i] = True

        self.wago.set("attctrl", valarr)

    def _status_read(self):
        """Read the status of the WAGO, Retunt the the active modules only.

        Returns:
            (str): String with the active modules. Empty string if none active.
        """
        mystr = ""
        value = self.pos_read()

        for i in range(self.nb_filter):
            if value & (1 << i) > 0:
                mystr += "%d " % i
        return mystr

    def status_read(self):
        """Read the status of the attenuators - human readbel representation.

        Retuns:
            (list): List of 2 strings - filter names, filter status.
        """
        log_debug(self, "In status_read()")
        stat = []
        mystr = ""
        lbl = "F"
        for i in range(self.nb_filter):
            mystr += lbl + str(i + 1) + "  "
        stat.append(mystr)

        value = self.pos_read()
        mystr = ""
        lbl1 = " "
        for i in range(self.nb_filter):
            if i > 8:
                lbl1 = "  "
            lbl = "OUT"
            if value & (1 << i) > 0:
                lbl = "IN "
            if value & (1 << i + self.nb_filter) > 0:
                lbl = "***"
            mystr += lbl + lbl1
        stat.append(mystr)
        return stat

    def matt_set(self, value):
        """Write the bit value in the WAGO. Check if the status corresponds
        to the value. First insert the new filters, than extract the rest.

        Args:
            value(int): The value, representing the addition of the active bits
        Raises:
            RuntimeError: Filters in unknown position
                          Timeout waiting for status to be the same as value.
        """
        log_debug(self, "In matt_set(%s)", value)
        oldvalue = self.pos_read()
        if oldvalue >= (1 << self.nb_filter):
            raise RuntimeError("Filters in unknown position")

        if oldvalue == value:
            return oldvalue

        # first insert the new filters, leave untouched the old ones
        if (~value & oldvalue) and (value & ~oldvalue):
            self.pos_write(value | oldvalue)
            sleep(0.25)

        # than redo to extract the old filters
        self.pos_write(value)

        check = self.pos_read()
        with Timeout(
            self.exec_timeout,
            RuntimeError("Timeout waiting for status to be %d" % value),
        ):
            while check != value:
                sleep(0.2)
                check = self.pos_read()

    def filter_set(self, filt, put_in):
        """Insert/extract a filter.

        Args:
            filt (int): Filter number (start from 0).
            put_in (bool): True if to be put in, False otherwise.
        Raises:
            RuntimeError: Filters in unknown position
                          Timeout waiting for filter to be inserted/extracted.
        """
        log_debug(self, "In filter_set(%s, %s)", filt, put_in)
        value = self.pos_read()
        if value >= (1 << self.nb_filter):
            raise RuntimeError("Filters in unknown position")
        _ff = 1 << filt
        if put_in is True:
            if (value & _ff) == 0:
                value += _ff
        else:
            if (value & _ff) != 0:
                value &= ~_ff
        self.pos_write(value)

        check = self.pos_read()
        with Timeout(
            self.exec_timeout,
            RuntimeError(
                "Timeout while waiting for filter to be %s"
                % ("in" if put_in is True else "out")
            ),
        ):
            while check != value:
                sleep(0.2)
                check = self.pos_read()

    def mattin(self, filt):
        """Insert a filter.

        Args:
            filt (int): Filter number (start from 0).
        """
        log_debug(self, "In mattin(%s)", filt)
        if filt >= self.nb_filter:
            raise RuntimeError("Wrong filter number %d" % filt)
        self.filter_set(filt, True)

    def mattout(self, filt):
        """Extract a filter.

        Args:
           filt (int): Filter number (start from 0).
        """
        log_debug(self, "In mattout(%s)", filt)
        if filt >= self.nb_filter:
            raise RuntimeError("Wrong filter number %d" % filt)
        self.filter_set(filt, False)

    def mattsetall(self, put_in):
        """Insert/extract al the filters.

        Args:
            put_in (bool): True if to be put in, False otherwise.
        """
        log_debug(self, "In mattsetall(%s)", put_in)
        value = 0
        if put_in:
            for i in range(self.nb_filter):
                value += 1 << i
        self.mattstatus_set(value)

    def mattstatus_get(self):
        """Read the bit value in the WAGO. The status represents the
        list of the active bits.

        Returns:
            (list): The status value as a list
        Raises:
            RuntimeError: Timeout waiting for status to be the same as value.
        """
        log_debug(self, "In mattstatus_get()")
        value = []
        value.append(self.pos_read())
        return value

    def mattstatus_set(self, value):
        """Write the bit value in the WAGO. Check if the status corresponds
        to the value.

        Args:
            value(int): The value, representing the addition of the active bits
        Raises:
            RuntimeError: Timeout waiting for status to be the same as value.
        """
        log_debug(self, "In mattstatus_set(%s)", value)
        self.pos_write(value)
        check = self.pos_read()
        with Timeout(
            self.exec_timeout,
            RuntimeError("Timeout waiting for status to be %d" % value),
        ):
            while check != value:
                sleep(0.2)
                check = self.pos_read()


class Matt:
    """Controller class"""

    def __init__(self, name, config):
        self.name = name
        wago_ip = config["controller_ip"]
        nb_filter = config["nb_filter"]
        # attenuator type (0,1 or 2, default is 0)
        att_type = config.get("att_type", 0)
        # wago card alternation (True or False, default False)
        wago_alternate = config.get("wago_alternate", False)
        # wago status module (default value "750-436")
        stat_m = config.get("status_module", "750-436")
        # wago control module (default value "750-530")
        ctrl_m = config.get("control_module", "750-530")

        self.exec_timeout = int(config.get("timeout", 5))

        self.__control = MattControl(
            wago_ip, nb_filter, att_type, wago_alternate, stat_m, ctrl_m
        )

        self.__control.connect()
        wrap_methods(self.__control, self)
