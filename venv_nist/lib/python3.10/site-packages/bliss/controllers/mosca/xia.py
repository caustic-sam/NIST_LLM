# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

import os
import numpy
import enum
from bliss.common.logtools import log_debug
from bliss.common.tango import DevFailed
from bliss.common.protocols import HasMetadataForScan
from bliss.controllers.mosca.base import McaController, TriggerMode

DetectorMode = enum.Enum("DetectorMode", "MCA MAP")


class FalconX(McaController, HasMetadataForScan):

    MCA_REFRESH_RATE_LIMIT = 0.01

    STATS_MAPPING = {
        # MOSCA    :  NxWriter
        "output": "events",
        "icr": "icr",
        "ocr": "ocr",
        "livetime": "trigger_livetime",
        "deadtime": "deadtime",
        "realtime": "realtime",
        "triggers": "triggers",
        "livetime_events": "energy_livetime",
        "deadtime_correction": "deadtime_correction",
    }

    def __info__(self):
        """
        Add specific information for XIA controllers.
        """
        txt = super().__info__()
        txt += f" detector mode:     {self.hardware.detector_mode}\n"
        txt += f" refresh rate:      {self.hardware.refresh_rate:.4f} s\n"

        txt += f"\n configuration file: {self.configuration_file}\n"

        # rois ?

        return txt

    def scan_metadata(self) -> dict:
        return {
            "@NX_class": "NXcollection",
            "configuration_file": self.configuration_file,
        }

    def _load_settings(self):
        super()._load_settings()

        # Use last configuration file kept in settings.
        config_path = self._settings.get("config_path")
        print("laod configpath from settings: ", config_path)

        if config_path:
            fdir, fname = os.path.split(config_path)  # split on linux separator
            try:
                print(f"set hwd cp={fdir} c={fname}")
                self.hardware.config_path = fdir
                self.hardware.config = fname
            except DevFailed as e:
                print("cannot load last configuration file:", e.args[0].desc)
                self._settings["config_path"] = None
        else:
            # Or read config from DS.
            config_path = self.configuration_file
            self._settings["config_path"] = config_path

    def _prepare_acquisition(self, acq_params):

        self.hardware.trigger_mode = acq_params["trigger_mode"]
        self.hardware.number_points = acq_params["npoints"]

        preset_time = acq_params["preset_time"]  # seconds

        if acq_params["trigger_mode"] == TriggerMode.SOFTWARE.name:

            # use given refresh_rate or 100ms by default
            refresh_rate = acq_params.setdefault("refresh_rate", 0.1)  # seconds

            # adjust refresh_rate if preset_time is smaller
            if preset_time <= refresh_rate:
                acq_params["refresh_rate"] = refresh_rate = preset_time

            self.refresh_rate = refresh_rate
            self.hardware.preset_value = preset_time * 1000  # milliseconds

        else:

            refresh_rate = acq_params.get("refresh_rate")  # seconds
            if refresh_rate is None:
                refresh_rate = self.hardware.refresh_rate
            else:
                self.refresh_rate = refresh_rate

            # auto tune number of pixels per buffer
            if preset_time <= 2 * refresh_rate:
                ppb_mini = int(numpy.ceil(2 * refresh_rate / preset_time)) + 1
            else:
                ppb_mini = 1

            ppb_default = max(ppb_mini, int(refresh_rate / preset_time))

            ppb = acq_params.get("map_pixels_per_buffer", ppb_default)

            log_debug(
                self,
                "mppb: %s, mppb_mini: %s, mppb_default: %s, refresh_rate: %s, preset_time: %s",
                ppb,
                ppb_mini,
                ppb_default,
                refresh_rate,
                preset_time,
            )

            self.hardware.map_pixels_per_buffer = ppb

    @property
    def refresh_rate(self):
        return self.hardware.refresh_rate

    @refresh_rate.setter
    def refresh_rate(self, value):
        if self.hardware.detector_mode == DetectorMode.MCA.name:
            if value < self.MCA_REFRESH_RATE_LIMIT:
                raise ValueError(
                    f"refresh rate must be >= {self.MCA_REFRESH_RATE_LIMIT}s in SOFTWARE trigger mode"
                )
        self.hardware.refresh_rate = value

    @property
    def detectors_identifiers(self):
        """return active detectors identifiers list [str] (['module:channel_in_module', ...])"""
        return self.hardware.channels_module_and_index

    @property
    def detectors_aliases(self):
        """return active detectors channels aliases list [int]"""
        return self.hardware.channels_alias

    """
    CONFIGURATION FILES
    self.hardware is a tango device proxy pointing to mosca DS.
    """

    @property
    def configuration_file(self):
        """
        Return path + filename of the CURRENT configuration file read from DS.
        """
        fdir = self.hardware.config_path  # Path
        fname = self.hardware.config  # File name (.ini)
        full_path = os.path.join(fdir, fname)  # NB: use unix separator in windows path.

        return full_path

    @configuration_file.setter
    def configuration_file(self, full_path):
        """
        <full_path>: str: path + name of the configuration file.
        Reload only if path or filename has changed. (why ? what if file.ini has benn changed without renaming ?)
        """
        fdir, fname = os.path.split(full_path)  # split on linux separator.

        print("fdir=", fdir)
        print("fname=", fname)

        doreload = False
        if fdir:
            if fdir != self.hardware.config_path:
                print(f"shcp = {fdir}")
                self.hardware.config_path = fdir
                doreload = True
        if fname:
            if fname != self.hardware.config:
                print(f"shc = {fname}")
                self.hardware.config = fname
                doreload = True

        print(f"scf = {self.configuration_file}")
        # Store path + filename read from DS in setting.
        self._settings["config_path"] = self.configuration_file

        if doreload:
            self.initialize()

    @property
    def default_configruation_file(self):
        """
        Return path + filename of DEFAULT configuration file.
        HUMMM: default and current configuration must have the same path ?
        """
        fdir = self.hardware.config_path
        fname = self.hardware.default_config

        # join with linux separator to be able to split later.
        full_path = os.path.join(fdir, fname)

        return full_path

    @default_configruation_file.setter
    def default_configruation_file(self, full_path):
        """
        Set the default configuration file
        * must change the tango ds config ?
        """
        raise NotImplementedError

    @property
    def available_configurations(self):
        """
        Return the list of configuration files found by Mosca device server.
        Read-only
        """
        # ??? not in mosca NO_SPEC ?
        try:
            _config_list = self.hardware.DevXiaGetConfigList()
        except Exception:
            _config_list = None
            print("Unable to read configuration list with command DevXiaGetConfigList")

        return _config_list

    def load_configuration(self, config_name=None):
        """
        If <config_name> is not None, load it.
        Otherwise, provide an interavtive menu to select a config to load.
        """
        from bliss.shell.getval import getval_idx_list

        if config_name is None:
            _config_list = self.available_configurations
            idx, config_name = getval_idx_list(
                _config_list, "Select configuration number"
            )

        # load config
        print(f"Loading {config_name}...")
        self.configuration_file = config_name
