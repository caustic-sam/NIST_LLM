# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
"""
Master class for filterset.

The child class must provide  definition for the filters with name/position/material/thickness.
Optionnally one can add either a density or a pair transmission/energy.
the units are :
 - thickness [mm]
 - density [g/cm3]
 - transmission [0. - 1.]
 - energy [keV]

Some examples of yml config:
With NO density:
---------------

- name: filtW0
  package: bliss.common.auto_filter.filterset_wheel
  class: FilterSet_Wheel
  rotation_axis: $att1
  filters:
    - name:Cu_0
      position: 0
      material: Cu
      thickness: 0
    - name:Cu_1
      position: 1
      material: Cu
      thickness: 0.04673608
    - name:Cu_2
      position: 2
      material: Cu
      thickness: 0.09415565
    - name:Cu_3
      position: 3
      material: Cu
      thickness: 0.14524267
    - name:Cu_4
      position: 4
      material: Cu
      thickness: 0.1911693

With Density:
-------------
- name: filtW0
  package: bliss.common.auto_filter.filterset_wheel
  class: FilterSet_Wheel
  rotation_axis: $att1
  filters:
    - name:Cu_0
      position: 0
      material: Cu
      thickness: 0
      density: 8.94

    - name:Mo_1
      position: 1
      material: Mo
      thickness: 0.055
      density: 10.22

With pairs transmission/energy:
------------------------------

- name: filtW0
  package: bliss.common.auto_filter.filterset_wheel
  class: FilterSet_Wheel
  rotation_axis: $att1
  filters:
    - name:Ag_0
      position: 0
      material: Ag
      thickness: 0.1
      transmission: 0.173
      energy: 16

    - name:Ag_1
      position: 1
      material: Ag
      thickness: 0.2
      transmission: 0.0412
      energy: 16

To Write a new filterset controller one should override these methods:
  * set_filter()
  * get_filter()
  * get_transmission()
  * build_filterset()
"""

import math
import numpy as np
import contextlib
from tabulate import tabulate

from bliss.common.logtools import log_info, log_debug
from bliss.common import event
from bliss import global_map
from bliss.common.user_status_info import status_message
from bliss.physics.materials import Compound
from bliss.config.settings import QueueSetting
from bliss.common.utils import autocomplete_property
from bliss.common.protocols import CounterContainer
from bliss.controllers.counter import SamplingCounterController
from bliss.common.counter import SamplingCounter, SamplingMode
from bliss.common.protocols import HasMetadataForScan


class FilterSet(CounterContainer, HasMetadataForScan):
    """
    This is mother class which should be inherited by any new filterset controller.
    The are some mandatory methods/properties to be overrided
    """

    def __init__(self, name, config):

        self.name = name
        global_map.register(self, tag=self.name)

        # Some data and caches
        self._fpattern = None
        self._ftransm = None
        self._abs_table = None
        self._min_cntrate = 1e3
        self._max_cntrate = 1e6
        self._nb_filters = 0
        self._curr_idx = -1
        self._filter_back = -1
        self._back = True
        self._nb_cycles = 0
        self._min_idx = 0
        self._max_idx = 0
        self._idx_inc = 0
        # default deadtime and peaking time for detector without
        # hw deadtime and peakingtime
        self._det_deadtime = 0
        self._det_deadtime_lim = 0.3
        self._det_peakingtime = 1e-6

        # Status message
        self._print = print

        self._config_filters = [dict(x) for x in config.get("filters", [])]
        if not self._config_filters:
            raise RuntimeError("Filter list is empty")

        self.energy_axis = config.get("energy_axis")
        if self.energy_axis is None:
            raise RuntimeError("Energy axis does not exist")

        self._last_energy = self.energy_axis.position
        if not self._last_energy > 0:
            raise RuntimeError(
                f"Filterset {name}: cannot calculate transmission, your energy is not valid: {self.energy_axis.position} keV"
            )

        # a short list of foils can be requested
        self.__enabled_filters = QueueSetting(f"{self.name}:short_list")
        sl = list(self.__enabled_filters)
        self._filter_positions = [f["position"] for f in self._config_filters]
        if len(sl) == 0:
            self.__enabled_filters.set(self._filter_positions)

        self._prepare_compounds()
        try:
            self._update_all()
        except RuntimeError:
            pass

        # Connect only once the filterset was built to avoid a race condition on build_filterset that
        # would be called both in the context of the object creation from config and from the energy axis update
        # triggered from redis
        event.connect(
            self.energy_axis, "state", self._update_all_from_energy_state_change
        )

        # create counters
        self._counters_controller = FilterSetCounterController(self)
        for cnt_conf in config.get("counters", list()):
            name = cnt_conf["counter_name"].strip()
            FilterSetCounter(name, cnt_conf, self._counters_controller)

    def __close__(self):
        if self.energy_axis:
            event.disconnect(
                self.energy_axis, "state", self._update_all_from_energy_state_change
            )

    def _update_all_from_energy_state_change(self, state):
        if "MOVING" not in state:
            self._update_all()

    @property
    def enabled_filters(self):
        """
        return the list of active filters
        """
        sl = list(self.__enabled_filters)
        if len(sl) == 0:
            return self._filter_positions
        return sl

    @enabled_filters.setter
    def enabled_filters(self, sl):
        if len(sl):
            for filt in sl:
                if filt not in self._filter_positions:
                    raise ValueError(
                        f"Your short list contains an invalid filter id (mask) {filt}"
                    )
        else:
            # empty list means all filters
            sl = self._filter_positions

        if not self.can_enable_filters(sl):
            raise ValueError(
                f"The provided filters cannot disable the current filter {self.get_filter()}"
            )

        if sl != list(self.__enabled_filters):
            self.__enabled_filters.set(sl)

            # now reinitialize the filterset to get new list of filters and absorption table
            self._update_all()

    def _update_all(self, energy=None):
        """
        (Re)calculate filter apparent densities and transmissions according to energy, enabled filters, ...
        """
        # recalculate the transmissions for each foil/filterset
        self._calc_transmissions(energy)

        # rebuild the filterset table
        self._nb_filters = self.build_filterset(list(self.__enabled_filters))

        # finally calculate the absorption table which take care of the min/max cnt rate
        self._calc_absorption_table()

    def __info__(self):
        self._calc_transmissions()
        info_str = f"\nCurrent filter = {self.filter}, transmission = {self.transmission:.5g} @ {self.energy:.5g} keV"
        info_str += f"\nEnabled filters  = {self.enabled_filters}"
        return info_str

    def info_table(self):
        """
        Return the information regarding the absorption table, the remaining effective filters
        which will be used during a scan.
        """
        table_info = []

        for filt in self._abs_table:
            table_info.append(list(filt))
        info = str(
            tabulate(
                table_info,
                headers=[
                    "Idx",
                    "Transm.",
                    "Max.cntrate",
                    "Opti.cntrate",
                    "Min.cntrate",
                ],
                floatfmt=".4g",
                numalign="left",
            )
        )
        info += "\n"
        return info

    @contextlib.contextmanager
    def _user_status(self):
        """
        Context to display filter information as a user status.
        i.e: During a scan it'll be displayed next to the progress-bar.
        """
        with status_message() as p:
            self._print = p
            try:
                yield p
            finally:
                self._print = print

    def _prepare_compounds(self):
        """Create the Compound object that will be used for calculating the transmission"""
        for filter in self._config_filters:
            material = filter.get("material")
            if not material:
                filter["compound"] = None
                continue
            c = filter["compound"] = Compound(material, density=filter.get("density"))
            # Overwrite density if transmission info is provided
            if "transmission" in filter:
                if "energy" not in filter:
                    raise ValueError(
                        f"filter {filter['name']} has transmission but missing the corresponding energy"
                    )
                else:
                    c.density_from_transmission(
                        filter["energy"],
                        filter["thickness"] / 10,
                        filter["transmission"],
                    )
            # Make sure the compound has a density
            if c.density is None:
                raise ValueError(
                    f"filter {filter['name']} has no density (specify directly or indirectly by the transmission)"
                )

    def _calc_transmissions(self, energy=None):
        """
        Calculate the transmission factors for the filters for the given energy
        """
        if energy is None:
            energy = self.energy_axis.position
        for filter_ in self._config_filters:
            filter_["transmission_calc"] = 1
            c = filter_.get("compound")
            if c is not None:
                filter_["transmission_calc"] = c.transmission(
                    energy, filter_["thickness"] / 10
                )[0]
        # save in setting the last energy
        self._last_energy = energy

    def _calc_absorption_table(self):
        """
        Regenerate the absorption table, which will be used to
        apply the best absorption to fit with the count-rate range
        """

        log_info(self, "Regenerating absorption table")

        self._nb_filtset = self._nb_filters

        min_trans = np.sqrt(self._min_cntrate / self._max_cntrate)
        max_trans = np.sqrt(min_trans)

        # the optimum count rate
        opt_cntrate = max_trans * self._max_cntrate

        log_info(self, f"min. transmission: {min_trans}")
        log_info(self, f"max. transmission: {max_trans}")
        log_info(self, f"opt. count rate: {opt_cntrate}")
        log_info(self, f"nb. filters: {self._nb_filters}")
        log_info(self, f"nb. filtset: {self._nb_filtset}")

        # Ok, the tricky loop to reduce the number of possible patterns according
        # to the current min. and max. transmission
        # Thanks to P.Fajardo, code copied for autof.mac SPEC macro set
        # It selects select only the patterns (fiterset)which fit with the transmission
        # range [min_trans,max_trans]

        d = 0
        nf = self._nb_filters
        for f in range(nf):
            s = self._ftransm[f:nf].argmax() + f
            pattern = self._fpattern[s]
            transm = self._ftransm[s]

            if transm == 0:
                break
            if s != f:
                self._fpattern[s] = self._fpattern[f]
                self._ftransm[s] = self._ftransm[f]
            if d == 0:
                pass
            elif d == 1:
                if (transm / self._ftransm[d - 1]) > max_trans:
                    continue
            else:
                if (transm / self._ftransm[d - 2]) > min_trans:
                    d -= 1
                elif (transm / self._ftransm[d - 1]) > max_trans:
                    continue
            if d != s:
                self._fpattern[d] = pattern
                self._ftransm[d] = transm

            d += 1

        # update filter number to the reduced one and resize both arrays
        self._nb_filtset = nfiltset = d
        self._fpattern = self._fpattern[0:d]
        self._ftransm = self._ftransm[0:d]

        log_info(self, f"New nb. filtset: {self._nb_filtset}")

        # Now calculate the absorption / deadtime data
        # array of nfilters columns and rows of:
        #  - [pattern, transmission, max_cntrate, opt_cntrate, min_cntrate]
        #
        self._abs_table = np.zeros([nfiltset, 5])
        data = self._abs_table
        data[0:nfiltset, 0] = self._fpattern[0:nfiltset]
        data[0:nfiltset, 1] = self._ftransm[0:nfiltset]

        # a quality will be calculted, 100% means all above retained patterns are useful
        self._quality = nfiltset
        for f in range(nfiltset):
            data[f, 2] = self._max_cntrate / data[f, 1]
            if f == 0:
                data[f, 3] = 0
                data[f, 4] = 0
            else:
                data[f, 3] = opt_cntrate / data[f - 1, 1]
                data[f, 4] = self._min_cntrate / data[f, 1]
                if data[f, 4] > data[f, 3]:
                    data[f, 4] = data[f, 3]
                    self._quality -= 1
        self._quality = 100 * self._quality / nfiltset
        log_info(self, f"Finally quality is {self._quality}")

    def sync(self, min_count_rate, max_count_rate, energy, back):
        """
        Update the absorption table (_abs_table) using the new cntrate
        range and the new energy.
        Check if the current filter is in the new table otherwise
        change it to the closest one.

        Return the effective number of filters
        """
        self._min_cntrate = min_count_rate
        self._max_cntrate = max_count_rate
        self._back = back

        # reset cycle number
        self._nb_cycles = 0

        # each time the energy change effective transmission and
        # a new absorption table are calculated

        log_info(self, f"Updating all with energy at {energy} keV")
        self._update_all(energy)

        # In case that the current filter is not in the abs. table
        # change to the closest one

        idx = self._read_filteridx()
        # _abs_table is a numpy array of float, so convert filter id to integer
        curr_filtid = int(self._abs_table[self._curr_idx, 0])
        if idx == -1:
            # of need to change with the closest filter
            # the new filter index is already stored in the cache attr. _curr_idx
            self.set_filter(curr_filtid)

        return self._nb_filtset

    def adjust_filter(self, count_time, counts):
        """
        Enfin the taken-decision method
        return True if the current filter is valid
        otherwise False
        """
        cntrate = counts / count_time
        log_debug(self, f"current count rate: {cntrate} cnt/s")

        # detector dead time not yet managed used default value
        # same thing for the deadtime limit and peakingtime
        # otherwise they should be read from a controller
        dtime = self._det_deadtime

        log_debug(self, f"current deadtime: {dtime} sec.")

        fidx = self._read_filteridx()
        log_debug(self, f"current filter index: {fidx}")

        # Which is the best filter corresponding to the current count rate
        optim = self._nb_cycles != 0
        new_fidx = self._find_filter(fidx, cntrate, dtime, optim)

        data = self._abs_table
        repeat = False
        new_filter = self.get_filter()

        if new_fidx != fidx:
            log_debug(
                self,
                f"need to change to data filter idx: {new_fidx} (filter {int(data[new_fidx, 0])})",
            )
            log_debug(self, f"min_idx: {self._min_idx} max_idx: {self._max_idx}")
            # use min max idx to find convergence and stop hysteresis
            if self._nb_cycles == 0:
                # first cycle
                if new_fidx < fidx:
                    self._min_idx = 0
                    self._max_idx = fidx
                    self._idx_inc = 0
                else:
                    self._min_idx = fidx
                    self._max_idx = self._nb_filtset - 1
                    self._idx_inc = 1
                new_filter = int(data[new_fidx, 0])
                repeat = True
            else:
                # sybsequent cycles
                if new_fidx < fidx:
                    self._max_idx = fidx - 1
                else:
                    self._min_idx = fidx + 1

                if new_fidx < self._min_idx:
                    new_fidx = self._min_idx
                elif new_fidx > self._max_idx:
                    new_fidx = self._max_idx

                if new_fidx == fidx:
                    log_debug(self, "convergence reached")
                    repeat = False
                else:
                    new_filter = int(data[new_fidx, 0])
                    repeat = True

        if repeat:
            self._nb_cycles += 1
            log_debug(self, "Repeating count")
            self._print(f"Autof: {fidx}->{int(data[new_fidx, 0])}")
        else:
            log_debug(self, "no filter change")
            self._nb_cycles = 0

        return (not repeat, new_filter)

    def _find_filter(self, fidx, cntrate, dtime, optim):
        """
        Look for the best filter for the countrate and deadtime  passed
        """
        dtime_lim = self._det_deadtime_lim
        nfiltset = self._nb_filtset
        data = self._abs_table

        if dtime > dtime_lim:
            if dtime < 1:
                dtime_cntr = -math.log(1 - dtime) / self._det_peakingtime
            else:
                dtime_cntr = 50 / self._det_peakingtime
            if dtime_cntr > cntrate:
                cntrate = dtime_cntr
        transm = data[fidx, 1]
        cntrate /= transm

        max_transm = transm * 10000
        for idx in range(fidx, nfiltset):
            if cntrate < data[idx, 2]:
                break
        if idx >= nfiltset:
            return nfiltset - 1

        pidx = idx
        for idx in range(pidx, -1, -1):
            if data[idx, 1] > max_transm:
                idx += 1
                break
            if optim and idx > 0 and cntrate < data[idx, 3]:
                continue
            if cntrate >= data[idx, 4]:
                break

        if idx < 0:
            return 0

        return idx

    def _read_filteridx(self):
        """
        Return current filter index
        If current filter is not in table, find the closest one in term of transmission
        Return -1 if the filter need to be changed and the new filter is stored in
        self._curr_idx for the calling function
        """

        filtid = self.get_filter()
        curridx = self._curr_idx
        if self._curr_idx < self._nb_filtset:
            currid = int(self._abs_table[curridx, 0])

            log_info(self, f"current filter id is {filtid}")
            if currid == filtid:
                return curridx

        trans = self.get_transmission(filtid)
        currtrans = 0
        nfiltset = self._nb_filtset

        found = False
        data = self._abs_table
        for idx in range(nfiltset):
            if filtid == int(data[idx, 0]):
                self._curr_idx = idx
                return idx
            trsm = data[idx, 1]
            if trsm <= trans and trsm > currtrans:
                currtrans = trsm
                curridx = idx
                found = True

        if not found:
            # be safe set the absorptionest filter
            curridx = nfiltset - 1

        log_info(self, f"Closest filter index is {curridx}")
        # put new
        self._curr_idx = curridx
        # return -1 means the filter must be changed
        return -1

    @property
    def energy(self):
        return self._last_energy

    @property
    def filter(self):
        """
        setter/getter for the filter
        """
        f = self.get_filter()
        t = self.get_transmission()
        self._print(f"Filter = {f}, transm = {t:.5g} @ {self.energy:.5g} keV")
        return f

    @property
    def position(self):
        """
        pure property to return the filter position
        can be used by ExpressionCalcCounter
        """
        return self.get_filter()

    @filter.setter
    def filter(self, new_filter):
        f = self.get_filter()
        if f != new_filter:
            self._print(
                f"Change filter {self.name} from {self.get_filter()} to {new_filter}"
            )
            self.set_filter(new_filter)
        else:
            self._print(f"Filter {self.name} is set to {new_filter}")

    @property
    def transmission(self):
        """
        Return the transmission factor of the current filter
        """
        return self.get_transmission()

    @property
    def filter_back(self):
        return self._filter_back

    @filter_back.setter
    def filter_back(self, bfilt):
        # humm should check if exist
        self._filter_back = bfilt

    def set_back_filter(self):
        """
        Set to back filter in any
        """
        if self._back:
            self.filter = self._filter_back

    @autocomplete_property
    def counters(self):
        return self._counters_controller.counters

    # ------------------------------------------------------------
    # Here start the methods to be overrided by the filterset
    # ------------------------------------------------------------

    def is_filter_enabled(self, filter_id):
        """Return true when the filter is enabled"""
        raise NotImplementedError

    def can_enable_filters(self, enabled_filters=None):
        """Return true when the current filter is part of the enabled filters"""
        raise NotImplementedError

    def set_filter(self, filter_id):
        """
        Set the new filter
        """
        raise NotImplementedError

    def get_filter(self):
        """
        Return the current filter index
        otherwise none is returned
        """
        raise NotImplementedError

    def get_transmission(self, filter_id=None):
        """
        Return the tranmission of filter
        if None, return the curent filter transmission
        """
        raise NotImplementedError

    def build_filterset(self, enabled_filters=None):
        """
        Build pattern and transmission arrays.
        A filterset, like Wago, is made of 4 real filters
        which can be combined to produce 15 patterns and transmissions.
        A filtersets like a wheel just provides 20 real filters and exactly
        the same amount of patterns and transmissions.
        Return the total number of effective filter combinations (filtset)
        """
        raise NotImplementedError
        return 0

    def get_filters(self):
        """
        Return the list of the public filters, a list of dictionnary items with at least:
        - position
        - density_calc
        - transmission_calc
        """
        return self._config_filters

    def scan_metadata(self):
        meta_dict = {"@NX_class": "NXattenuator"}
        meta_dict["position"] = self.position
        meta_dict["transmission"] = self.transmission
        return meta_dict


class FilterSetCounterController(SamplingCounterController):
    """
    Manages filterset counters with following tags:
    - filteridx: position of the filterset
    - transmission: transmission of the filterset
    """

    def __init__(self, filterset):

        super().__init__(filterset.name)
        self.filterset = filterset
        global_map.register(filterset, parents_list=["counters"])

        self.tags = {"filteridx": int, "transmission": float}

    def read_all(self, *counters):
        values = []
        for cnt in counters:
            if cnt.tag == "filteridx":
                values.append(self.filterset.get_filter())
            elif cnt.tag == "transmission":
                values.append(self.filterset.get_transmission())
        return values


class FilterSetCounter(SamplingCounter):
    def __init__(self, name, config, controller):

        self.tag = config["tag"]

        if self.tag not in controller.tags:
            raise RuntimeError(
                f"filterset {controller.filterset.name}: counter {name} tag {self.tag} is not supported, only {controller.tags}"
            )
        dtype = controller.tags[self.tag]

        SamplingCounter.__init__(
            self, name, controller, mode=SamplingMode.SINGLE, dtype=dtype
        )
