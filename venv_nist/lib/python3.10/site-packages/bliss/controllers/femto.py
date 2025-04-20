# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
FEMTO dlpca-200 Low Noise Current Amplifier

Using a wago box to drive the femto amplifier

Example yml file:

.. code-block::

  plugin: bliss
  module: femto
  class: Femto
  - name:     f1
    wago:     $wcid32c
    type:     dlpca
    sig_gain:     f1gain  # (3)  750-519, mandatory
    sig_range:    f1hilo  # (1)  750-519, mandatory
    sig_overload: f1ovl   # (1)  750-414, mandatory
    sig_offset:   f1oset  # (1)  750-556, optional
    sig_coupling: dc      #               optional
    # possible other values could be:
    # sig_coupling: f1acdc
    # sig_coupling: dc
    # sig_coupling: ac
    # sig_bandwidth: f1bw10 f1bw1 #       optional
"""

from bliss.common.logtools import log_warning, log_debug, log_error
from bliss.config.beacon_object import BeaconObject
from bliss.scanning.scan_meta import HasMetadataForScan


def _get_wago_channel_names(wago, name_filter="None"):
    if not name_filter:
        name_filter = ""
    _channels = wago.logical_keys
    _channels = [x for x in _channels if name_filter in x]
    return _channels


class Femto(BeaconObject, HasMetadataForScan):
    """
    Femto Controller.
    """

    name = BeaconObject.config_getter("name")

    _FEMTO_TYPE = ["dhcpa", "dlpca", "ddpcs"]
    _FEMTO_RANGE = {
        "dhcpa": {"min": 10e2, "max": 10e8},
        "dlpca": {"min": 10e3, "max": 10e11},
        "ddpcs": {"min": 10e4, "max": 10e13},
    }
    _FEMTO_GAIN = [
        10e2,
        10e3,
        10e4,
        10e5,
        10e6,
        10e7,
        10e8,
        10e9,
        10e10,
        10e11,
        10e12,
        10e13,
    ]
    _FEMTO_BANDWIDTH = ["Full", "1MHz", "10MHz"]
    _FEMTO_GAINRANGE = ["low", "high"]

    def __init__(self, name, config_tree):
        """initialise the femto amplifier"""
        # share_hardware default value is True, but force it there.
        super().__init__(config_tree, share_hardware=True)

        self._channels = []

        self._config = config_tree

        # check for mandatory properties
        for prop in ["type", "wago", "sig_gain", "sig_range", "sig_overload"]:
            try:
                _ = config_tree[prop]
            except KeyError as error:
                emsg = "Missing mandatory config item %s !" % (error)
                log_warning(self, emsg)
                raise Exception(emsg) from error

        # check for optional properties
        for prop in ["sig_offset", "sig_coupling", "sig_bandwidth"]:
            try:
                _ = config_tree[prop]
            except KeyError:
                pass

        self.wago = self._config["wago"]
        self.type = self._config["type"]
        try:
            self._FEMTO_TYPE.index(self.type)
        except ValueError as val_err:
            raise Exception(
                "Bad femto type %s. Use one of %s !"
                % (self.type, ",".join(self._FEMTO_TYPE))
            ) from val_err

        # get the configured channel from this wagobox
        self._channels = _get_wago_channel_names(self.wago, "")

        self._sig_offset = None
        self._sig_coupling = None
        self._fixacdc = None
        self._sig_gain = None
        self._sig_range = None
        self._sig_overload = None
        self._sig_bandwidthlow = None
        self._sig_bandwidthhigh = None

        # create a set with the names of signals
        log_debug(self, "femto -- channels: %s" % (self._channels))
        if not self._channels:  # and complain, if they can't be found
            emsg = "Missing channels on wago controller %s !" % (self.wago)
            log_error(self, emsg)
            raise Exception(emsg)

        try:
            self._sig_offset = self._config["sig_offset"]
        except KeyError as error:
            log_warning(self, "config item %s not available !" % error)
        else:
            if self._sig_offset not in self._channels:
                self._sig_offset = None

        # not all femtos have coupling !
        # is it using AC or DC (not seen on ID32)
        try:
            self._sig_coupling = self._config["sig_coupling"]
        except KeyError:
            # log_warning(self, "config item %s not available !" % error)
            pass
        else:
            if self._sig_coupling.lower() == "ac" or self._sig_coupling.lower() == "dc":
                self._fixacdc = self._sig_coupling
                self._sig_coupling = None
            if self._sig_coupling not in self._channels:
                self._sig_coupling = None

        try:
            self._sig_bandwidthhigh, self._sig_bandwidthlow = self._config[
                "sig_bandwidth"
            ].split()
        except KeyError:
            # log_warning(self, "config item %s not available !" % error)
            pass
        else:
            if (
                self._sig_bandwidthhigh not in self._channels
                or self._sig_bandwidthlow not in self._channels
            ):
                self._sig_bandwidthhigh = self._sig_bandwidthlow = None

        # All have the following.
        try:
            self._sig_gain = self._config["sig_gain"]
        except KeyError as error:
            log_error(self, "config item %s not available !" % error)
            raise error
        try:
            self._sig_range = self._config["sig_range"]
        except KeyError as error:
            log_error(self, "config item %s not available !" % error)
            raise error
        try:
            self._sig_overload = self._config["sig_overload"]
        except KeyError as error:
            log_error(self, "config item %s not available !" % error)
            raise error

    def __info__(self):
        info = f"Femto ({self.type.upper()}), Wago: {self.wago.name}\n"
        info += f"  - range:  {self.range}, gain: {self.gain:g}\n"
        info += f"  - offset: {self.offset:.4g}, overload: {self.overload}"
        return info

    @BeaconObject.property()
    def gain(self):
        """return the calculated gain from the 3 or 4 cells
        read from the wago
        """
        gainarr = self.wago.get(self._sig_gain)
        # an array of 3 or 4 floats depending on type
        nmem = len(gainarr)
        # if self.type is "ddpca": # we get 4 values
        # In earlier version we were using a C++ tango device server
        # silly thing, delivers floats, they are integers !!!
        # gainarr = gainarr.astype(int)
        log_debug(
            self,
            "femto %s is of type %s and returns %d values : %s"
            % (self.name, self.type, len(gainarr), gainarr),
        )
        gain = 0
        for i in range(nmem):
            gain += int(gainarr[i]) * pow(2, i)
        log_debug(self, "femto %s gain: %d" % (self.name, gain))

        fhilo = None
        # take into account the gain range
        if self.type != "ddpca":
            fhilo = self.range
        log_debug(self, "femto %s type %s: range %s" % (self.name, self.type, fhilo))
        if self.type == "dhpca":
            if self._FEMTO_GAINRANGE.index(fhilo):
                gain += 1
                log_debug(self, "%s: type %s increase by one" % (self.name, self.type))
        elif self.type == "dlpca":
            if self._FEMTO_GAINRANGE.index(fhilo):
                log_debug(
                    self, "%s: type %s increase by three" % (self.name, self.type)
                )
                gain += 3
            else:
                gain += 1
                log_debug(self, "%s: type %s increase by ONE" % (self.name, self.type))
        elif self.type == "ddpca":
            gain += 2
            log_debug(self, "%s: type %s increase by TWO" % (self.name, self.type))
        log_debug(self, "femto %s gain: %d" % (self.name, gain))
        return self._FEMTO_GAIN[gain]

    @gain.setter
    def gain(self, value):
        """
        Set the gain property.
        """
        if self._sig_gain:
            indnum = self._FEMTO_GAIN.index(value)
            log_debug(self, "Setter: Gain %d using index %d" % (value, indnum))

            gainarr = self.wago.get(self._sig_gain)
            nmem = len(gainarr)

            # an array of 3 or 4 floats depending on type
            fhilo = self.range
            # take into account the gain range
            if self.type == "dhpca":
                if not fhilo and indnum > 5:
                    log_error(self, "Error: gain too high for the current gain range")
                    log_error(self, "Hint : change range")
                    return
                if fhilo and indnum < 1:
                    log_error(self, "Error: gain too low for the current gain range")
                    log_error(self, "Hint : change range")
                    return
                if fhilo:
                    indnum -= 1
            elif self.type == "dlpca":
                if value < 10e3 or value > 10e11:
                    log_error(
                        self, "Desired value not in the allowed range of 10e3 to 10e11."
                    )
                    return
                """
                    Gain setting for dlpca-200

                    Low noise    |  High speed
                    Pin 14=HIGH  |  Pin 14=LOW   |  Pin 12  |  Pin 11  |  Pin 10
                    Gain (V/A)   |  Gain (V/A)   |  MSB     |          |  LSB
                    ------------------------------------------------------------
                        10e3     |    10e5       |  LOW     |   LOW    |  LOW
                        10e4     |    10e6       |  LOW     |   LOW    |  HIGH
                        10e5     |    10e7       |  LOW     |   HIGH   |  LOW
                        10e6     |    10e8       |  LOW     |   HIGH   |  HIGH
                        10e7     |    10e9       |  HIGH    |   LOW    |  LOW
                        10e8     |    10e10      |  HIGH    |   LOW    |  HIGH
                        10e9     |    10e11      |  HIGH    |   HIGH   |  LOW
                """
                if self._FEMTO_GAINRANGE.index(fhilo):
                    log_debug(
                        self,
                        'Setter: type "dlpca", range %s, gain %d sending %d'
                        % (fhilo, value, indnum),
                    )
                    if indnum > 9:
                        log_error(
                            self, "Error: gain too high for the current gain range"
                        )
                        log_error(self, "Hint : change range")
                        return
                    elif indnum < 3:
                        log_error(
                            self, "Error: gain too low for the current gain range"
                        )
                        log_error(self, "Hint : change range")
                        return
                    log_debug(
                        self,
                        "2Setter: Gain %d sending %d - %s"
                        % (value, indnum, (indnum < 1)),
                    )
                    # shift the given index number to get the index to program
                    indnum -= 3
                else:
                    log_debug(
                        self,
                        'Setter: type "dlpca", range %s, gain %d sending %d'
                        % (fhilo, value, indnum),
                    )
                    if indnum > 7:
                        log_error(
                            self, "Error: gain too high for the current gain range"
                        )
                        log_error(self, "Hint : change range")
                        return
                    elif indnum < 1:
                        log_error(
                            self, "Error: gain too low for the current gain range"
                        )
                        log_error(self, "Hint : change range")
                        return
                    log_debug(
                        self,
                        "2Setter: Gain %d sending %d - %s"
                        % (value, indnum, (indnum < 1)),
                    )
                    # shift the given index number to get the index to program
                    indnum -= 1
            elif self.type == "ddpca":
                indnum -= 2
                if indnum < 0:
                    log_error(self, "Error: gain too low for the current gain range")
                    log_error(self, "Hint : change range")
                    return

            # generate the bit mask for the WAGO channels
            for _ in range(nmem):
                log_debug(self, "**** %d - %s" % (_, (indnum & pow(2, _))))
                gainarr[_] = indnum & pow(2, _) > 0
            log_debug(self, gainarr)
            self.wago.set(self._sig_gain, gainarr)

    # not all femtos have an offset !
    @BeaconObject.property()
    def offset(self):
        """returns the offset of the femto amplifier"""
        if self._sig_offset:
            retval = self.wago.get(self._sig_offset)
            return retval
        else:
            return 0

    @offset.setter
    def offset(self, value):
        """sets the offset of the femto amplifier"""
        if not 0 <= value <= 10:
            raise ValueError("Offset must be between 0 and 10.")

        if self._sig_offset:
            self.wago.set(self._sig_offset, value)
        else:
            log_error(self, "This femto does not have an offset!")

    # not all femtos have coupling !
    @BeaconObject.property()
    def coupling(self):
        """return the coupling factor set in the wago or None, if
        non-existant
        """
        if self._fixacdc:
            # either ac or dc
            return self._fixacdc
        log_debug(self, "self.sig_coupling is %s" % self._sig_coupling)
        if self._sig_coupling:
            retval = self.wago.get(self._sig_coupling)
            return retval
        else:
            return None

    @coupling.setter
    def coupling(self, value):
        """sets the  femto amplifier's coupling, if it is not fixed"""
        if value in ("ac", "dc"):
            self._fixacdc = value
            return
        log_debug(self, "self._couplingstr is %s" % self._sig_coupling)
        if self._sig_coupling:
            self.wago.set(self._sig_coupling, value)
        else:
            log_error(self, "This femto does not have an offset!")

    @BeaconObject.property()
    def bandwidth(self):
        """sets the femto amplifier's bandwith"""
        log_debug(self, "self._bandwidthhigh is %s" % self._sig_bandwidthhigh)
        if self._sig_bandwidthhigh:
            low = self.wago.get(self._sig_bandwidthlow)
            high = self.wago.get(self._sig_bandwidthhigh)
            retval = (high << 1) + low
            return self._FEMTO_BANDWIDTH[retval]

    @bandwidth.setter
    def bandwidth(self, value):
        """returns the  femto amplifier's coupling, if it is not fixed"""
        if self._sig_bandwidthhigh:
            # compare lower case only, between list and value
            indnum = [item.lower() for item in self._FEMTO_BANDWIDTH].index(
                value.lower()
            )  # self.FEMTO_BANDWIDTH.index(value)
            high = indnum >> 1
            low = indnum & 1
            self.wago.set(self._sig_bandwidthhigh, high)
            self.wago.set(self._sig_bandwidthlow, low)
        else:
            log_error(self, "This femto does not use a bandwidth!")

    @BeaconObject.property()
    def range(self):
        """returns the range setting from the wago"""
        # log_debug(self, "Property: Range str %s" % self.sig_range)
        if self._sig_range:
            retval = int(self.wago.get(self._sig_range))
            log_debug(self, "Result range %s" % retval)
            return self._FEMTO_GAINRANGE[retval]
        return None

    @range.setter
    def range(self, value):
        """sets the femto amplifier's range"""
        if self._sig_range:
            indnum = [item.lower() for item in self._FEMTO_GAINRANGE].index(
                value.lower()
            )
            log_debug(self, "Setter: Range %s sending %d" % (value, indnum))

            self.wago.set(self._sig_range, indnum)
        else:
            log_error(self, "This femto does not have use range!")

    @BeaconObject.property()
    def overload(self):
        """return the femto amplifier's overload state. Read-only property"""
        # log_debug(self, "Property: overload str %s" % self.sig_overload)
        if self._sig_overload:
            retval = int(self.wago.get(self._sig_overload))
            log_debug(self, "Result overload %s" % retval)
            return "off" if retval == 0 else "on"

    @overload.setter
    def overload(self, value):
        pass

    def scan_metadata(self):
        metadata = dict()
        metadata["@NX_class"] = "NXcollection"
        metadata["coupling"] = self.coupling
        metadata["gain"] = self.gain
        metadata["offset"] = self.offset
        metadata["range"] = self.range
        return metadata
