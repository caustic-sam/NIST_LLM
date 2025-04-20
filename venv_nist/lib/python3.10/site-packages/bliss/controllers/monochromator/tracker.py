# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
???
"""

import functools
from datetime import datetime
import numpy
import tabulate

import xcalibu

from bliss.config.conductor import client
from bliss.config import settings
from bliss.common.utils import (
    autocomplete_property,
)


class EnergyTrackingObject:
    def __init__(self, config):

        self._config = config
        self._name = config["name"]
        self._mono = None

        self._motors = {}
        self._parameters = {}

        self._load_config()

        for axis in self._motors.values():
            self._parameters[axis.name]["obj"] = TrackerMotor(axis, self)
            setattr(axis, "tracking", self._parameters[axis.name]["obj"])
            setattr(self, axis.name, self._parameters[axis.name]["obj"])

    def _set_mono(self, mono):
        self._mono = mono

    def _reload_config(self):
        self._config.reload()
        self._load_config()

    def _load_config(self):

        trackers_config = self._config.get("trackers")
        for tracker in trackers_config:
            # Motor
            axis = tracker.get("motor", None)
            if axis is None:
                raise RuntimeError("EnergyTrackingObject: No motor given")
            self._motors[axis.name] = axis
            self._parameters[axis.name] = {}
            # Tracking State
            self._parameters[axis.name]["tracked"] = settings.SimpleSetting(
                f"EnergyTrackingObject_{axis.name}_tracked", default_value=False
            )
            # Default_mode
            default_mode = tracker.get("default_mode", None)
            if default_mode is not None and default_mode not in [
                "polynom",
                "table",
                "theory",
            ]:
                raise RuntimeError(
                    "EnergyTrackingObject: default_mode must be one of [polynom, table, theory]"
                )
            self._parameters[axis.name]["selected_mode"] = settings.SimpleSetting(
                f"EnergyTrackingObject_{axis.name}_selected_mode", default_value=""
            )

            # scanning mode
            common_scanning_mode = tracker.get("scanning_mode", "MOVING")

            # constants
            constants = tracker.get("constants", None)
            if constants is not None:
                self._parameters[axis.name]["constant"] = constants[0].to_dict()

            # Parameters
            parameters = tracker.get("parameters", None)
            if parameters is None:
                raise RuntimeError(
                    f"EnergyTrackingObject: axis {axis.name} has no parameters"
                )

            # Selected id
            self._parameters[axis.name]["selected_id"] = settings.SimpleSetting(
                f"EnergyTrackingObject_{axis.name}_seleted_id", default_value=""
            )
            self._parameters[axis.name]["param_id"] = {}
            for param in parameters:
                param_id = param.get("id", None)
                if param_id is None:
                    raise RuntimeError(
                        f"EnergyTrackingObject: axis {axis.name} has no id for parameter"
                    )
                self._parameters[axis.name]["param_id"][param_id] = {}
                if self._parameters[axis.name]["selected_id"].get() == "":
                    self._parameters[axis.name]["selected_id"].set(param_id)

                # scanning_mode
                scanning_mode = param.get("scanning_mode", common_scanning_mode)
                self._parameters[axis.name]["param_id"][param_id][
                    "scanning_mode"
                ] = settings.SimpleSetting(
                    f"EnergyTrackingObject_{axis.name}_scanning_mode",
                    default_value=scanning_mode,
                )
                self._parameters[axis.name]["param_id"][param_id]["scanning_mode"].set(
                    scanning_mode
                )

                # fill polynom coefficients
                polynom = param.get("polynom", None)
                self._parameters[axis.name]["param_id"][param_id]["polynom"] = None
                if polynom is not None:
                    if self._parameters[axis.name]["selected_mode"].get() == "":
                        self._parameters[axis.name]["selected_mode"].set("polynom")
                    self._parameters[axis.name]["param_id"][param_id]["polynom"] = {}
                    for coef in ["E6", "E5", "E4", "E3", "E2", "E1", "E0"]:
                        coef_value = float(polynom.get(coef, 0.0))
                        self._parameters[axis.name]["param_id"][param_id]["polynom"][
                            coef
                        ] = coef_value
                    self._parameters[axis.name]["param_id"][param_id]["polynom"][
                        "min"
                    ] = float(polynom.get("min", 0.0))
                    self._parameters[axis.name]["param_id"][param_id]["polynom"][
                        "max"
                    ] = float(polynom.get("max", 0.0))
                    self._build_polynom_calib(axis, param_id)

                # fill theory coefficients
                theory = param.get("theory", None)
                self._parameters[axis.name]["param_id"][param_id]["theory"] = None
                if theory is not None:
                    if self._parameters[axis.name]["selected_mode"].get() == "":
                        self._parameters[axis.name]["selected_mode"].set("theory")
                    self._parameters[axis.name]["param_id"][param_id]["theory"] = {
                        "axis": axis
                    }
                    for item in theory:
                        for key, val in item.items():
                            self._parameters[axis.name]["param_id"][param_id]["theory"][
                                key
                            ] = val
                # calibration table
                table_file = param.get("table", None)
                self._parameters[axis.name]["param_id"][param_id]["table"] = None
                if table_file is not None:
                    if self._parameters[axis.name]["selected_mode"].get() == "":
                        self._parameters[axis.name]["selected_mode"].set("table")
                    self._parameters[axis.name]["param_id"][param_id]["table"] = {
                        "file": table_file
                    }
                    self._build_file_calib(axis, param_id)

    def __info__(self):
        if self._mono is not None:
            bragg = self._mono.motors["bragg"].position
            energy = self._mono.bragg2energy(bragg)

            #
            # TITLE
            #
            title = [""]
            for axis in self._motors.values():
                title.append(axis.name)

            #
            # CALCULATED POSITION ROW
            #
            calculated = ["Calculated Pos."]
            for axis in self._motors.values():
                track = self._energy2tracker(axis, energy)
                calculated.append(f"{track:.3f} {axis.unit}")

            #
            # CURRENT POSITION ROW
            #
            current = ["Current Pos."]
            for axis in self._motors.values():
                current.append(f"{axis.position:.3f} {axis.unit}")

            #
            # TRACKING STATE ROW
            #
            tracking = ["Tracking State"]
            for axis in self._motors.values():
                tracking.append("ON" if axis.tracking.state else "OFF")

            #
            # MODE ROW
            #
            mode = ["Mode"]
            for axis in self._motors.values():
                mode.append(axis.tracking.mode.get())

            #
            # PARAM ID ROW
            #
            param_id = ["Parameter id"]
            for axis in self._motors.values():
                param_id.append(axis.tracking.param_id.get())

            mystr = tabulate.tabulate(
                [calculated, current, tracking, mode, param_id],
                headers=title,
                tablefmt="plain",
                stralign="right",
            )

            return mystr
        else:
            return "Monochromator Object is not set\n"

    def all_on(self):
        for axis in self._motors.values():
            axis.tracking.on()

    def all_off(self):
        for axis in self._motors.values():
            axis.tracking.off()

    def all_param_id(self, param_id):
        for axis in self._motors.values():
            param_ids = self._parameters[axis.name]["param_id"]
            if param_ids is not None:
                if param_id in param_ids.keys():
                    axis.tracking.mode.set(param_id)

    def all_mode(self, mode):
        for axis in self._motors.values():
            selected_id = axis.tracking.param_id.get()
            modes = self._parameters[axis.name]["param_id"][selected_id]
            if modes is not None:
                if mode in modes.keys():
                    axis.tracking.mode.set(mode)

    def _get_selected_scanning_mode(self, tracker_mot):
        selected_id = tracker_mot.tracking.param_id.get()
        return self._parameters[tracker_mot.name]["param_id"][selected_id][
            "scanning_mode"
        ].get()

    def _set_selected_scanning_mode(self, tracker_mot, scanning_mode):
        if scanning_mode in ["MOVING", "FIXED", "MIDDLE"]:
            selected_id = tracker_mot.tracking.param_id.get()
            self._parameters[tracker_mot.name]["param_id"][selected_id][
                "scanning_mode"
            ].set(scanning_mode)

    #
    # Table
    #
    def _build_file_calib(self, axis, param_id):
        """
        load calibration table from file.
        """
        table_file = self._parameters[axis.name]["param_id"][param_id]["table"]["file"]
        # tmp_calib_file = f"/tmp/tmp_calib_file_{axis.name}"

        # with open(table_file, "w") as xcalib:
        #    content = client.get_config_file(table_file).decode("utf-8")
        #    xcalib.write(content)

        # should work with:
        # calib = xcalibu.Xcalibu(calib_file_name=tmp_calib_file,
        #                         calib_type="TABLE",
        #                         reconstruction_method="INTERPOLATION")

        calib = xcalibu.Xcalibu()
        calib.set_calib_name(axis.name)
        calib.set_calib_time(0)
        calib.set_calib_file_name(table_file)
        calib.set_calib_type("TABLE")
        calib.set_reconstruction_method("INTERPOLATION", "linear")
        calib.load_calib()
        calib.check_monotonic()
        calib.compute_interpolation()

        self._parameters[axis.name]["param_id"][param_id]["table"][
            "calib"
        ] = TrackTable(axis, calib=calib, filename=table_file)

    def _get_track_from_table(self, axis_name, param_id, energy):
        if axis_name not in self._motors.keys():
            raise ValueError(
                f"EnergyTrackingObject->_get_track_from_table: {axis_name} is not a tracker"
            )
        param_ids = self._parameters[axis_name]["param_id"]
        if param_ids is None or param_id not in param_ids.keys():
            raise ValueError(
                f"EnergyTrackingObject->_get_track_from_table: id {param_id} is not a valid id for {axis_name}"
            )
        calib = param_ids[param_id]["table"]["calib"]

        ene_arr = numpy.atleast_1d(energy)

        track_arr = calib.calib.get_y(ene_arr)

        if ene_arr.size == 1:
            return track_arr[0]

        return track_arr

    def _get_energy_from_table(self, axis_name, param_id, track):
        if axis_name not in self._motors.keys():
            raise ValueError(
                f"EnergyTrackingObject->_get_track_from_table: {axis_name} is not a tracker"
            )
        param_ids = self._parameters[axis_name]["param_id"]
        if param_ids is None or param_id not in param_ids.keys():
            raise ValueError(
                f"EnergyTrackingObject->_get_track_from_table: id {param_id} is not a valid id for {axis_name}"
            )
        calib = param_ids[param_id]["table"]["calib"]

        track_arr = numpy.atleast_1d(track)

        ene_arr = calib.calib.get_x(track_arr)

        if track_arr.size == 1:
            return ene_arr[0]

        return ene_arr

    """
    Polynom
    """

    def _build_polynom_calib(self, axis, param_id):
        pobj = self._parameters[axis.name]["param_id"][param_id]["polynom"]
        polynom_coef = [
            pobj["E0"],
            pobj["E1"],
            pobj["E2"],
            pobj["E3"],
            pobj["E4"],
            pobj["E5"],
            pobj["E6"],
        ]

        calib = xcalibu.Xcalibu()
        calib.set_calib_name(axis.name)
        calib.set_calib_time(0)
        calib.set_calib_type("POLY")
        calib.set_coeffs(polynom_coef)
        calib.set_x_limits(pobj["min"], pobj["max"])
        calib.set_reconstruction_method("INTERPOLATION")
        calib.check_monotonic()
        calib.compute_interpolation()

        self._parameters[axis.name]["param_id"][param_id]["polynom"]["calib"] = calib

    def _get_track_from_polynom(self, axis_name, param_id, energy):
        if axis_name not in self._motors.keys():
            raise ValueError(
                f"EnergyTrackingObject->_get_track_from_polynom: {axis_name} is not a tracker"
            )
        param_ids = self._parameters[axis_name]["param_id"]
        if param_ids is None or param_id not in param_ids.keys():
            raise ValueError(
                f"EnergyTrackingObject->_get_track_from_polynom: id {param_id} is not a valid id for {axis_name}"
            )
        calib = param_ids[param_id]["polynom"]["calib"]

        ene_arr = numpy.atleast_1d(energy)

        track_arr = calib.get_y(ene_arr)

        if ene_arr.size == 1:
            return track_arr[0]

        return track_arr

    def _get_energy_from_polynom(self, axis_name, param_id, track):
        if axis_name not in self._motors.keys():
            raise ValueError(
                f"EnergyTrackingObject->_get_track_from_table: {axis_name} is not a tracker"
            )
        param_ids = self._parameters[axis_name]["param_id"]
        if param_ids is None or param_id not in param_ids.keys():
            raise ValueError(
                f"EnergyTrackingObject->_get_track_from_table: id {param_id} is not a valid id for {axis_name}"
            )
        calib = param_ids[param_id]["polynom"]["calib"]

        track_arr = numpy.atleast_1d(track)

        ene_arr = calib.get_x(track_arr)

        if track_arr.size == 1:
            return ene_arr[0]

        return ene_arr

    """
    Theory
    """

    def _get_track_from_theory(self, axis_name, param_id, energy):
        if self._parameters[axis_name]["param_id"][param_id]["theory"] is not None:
            func_name = self._parameters[axis_name]["param_id"][param_id]["theory"][
                "energy2tracker"
            ]
            meth = getattr(self, func_name)
            ene = numpy.copy(numpy.array(energy))
            tracker = meth(
                ene, self._parameters[axis_name]["param_id"][param_id]["theory"]
            )
            return tracker
        else:
            raise RuntimeError("No method given to calculate tracker in Theory mode")

    def _get_energy_from_theory(self, axis_name, param_id, track):
        if self._parameters[axis_name]["param_id"][param_id]["theory"] is not None:
            func_name = self._parameters[axis_name]["param_id"][param_id]["theory"][
                "tracker2energy"
            ]
            meth = getattr(self, func_name)
            track_arr = numpy.copy(numpy.array(track))
            energy = meth(
                track_arr, self._parameters[axis_name]["param_id"][param_id]["theory"]
            )
            return energy
        else:
            raise RuntimeError("No method given to calculate tracker in Theory mode")

    """
    Conversion methods
    """

    def _energy2tracker(self, axis, energy):
        track = numpy.nan
        if axis in self._motors.values():
            selected_id = axis.tracking.param_id.get()
            selected_mode = axis.tracking.mode.get()
            if selected_id is not None and selected_mode is not None:
                if selected_mode == "polynom":
                    track = self._get_track_from_polynom(axis.name, selected_id, energy)
                if selected_mode == "table":
                    track = self._get_track_from_table(axis.name, selected_id, energy)
                if selected_mode == "theory":
                    track = self._get_track_from_theory(axis.name, selected_id, energy)
        return track

    def _tracker2energy(self, axis, track):
        energy = numpy.nan
        if axis in self._motors.values():
            selected_id = axis.tracking.param_id.get()
            selected_mode = axis.tracking.mode.get()
            if selected_id is not None and selected_mode is not None:
                if selected_mode == "polynom":
                    energy = self._get_energy_from_polynom(
                        axis.name, selected_id, track
                    )
                if selected_mode == "table":
                    energy = self._get_energy_from_table(axis.name, selected_id, track)
                if selected_mode == "theory":
                    energy = self._get_energy_from_theory(axis.name, selected_id, track)
        return energy


class TrackTable:
    """
    helper class to:
    * plot calibration
    * add/remove points to the calibration
    * modify the calibration file
    """

    def __init__(self, axis, calib=None, filename=None, kev_tolerance=0.01):
        self.__calib = calib
        self._filename = filename
        self._axis = axis
        self._kev_tolerance = kev_tolerance
        self._backup = True

    @autocomplete_property
    def calib(self):
        return self.__calib

    @calib.setter
    def calib(self, calib):
        self.__calib = calib

    def save(self):
        """Save table calib file to beamline configuration"""
        if self._backup:
            # suffix current file with the date of the day
            content = client.get_config_file(self._filename).decode("utf-8")
            today = datetime.today().strftime("%Y%m%d")
            client.set_config_db_file(f"{self._filename}.{today}", content)

        self.calib.save()
        with open(self.calib.get_calib_file_name(), "r") as calib_file:
            content = calib_file.read()
        client.set_config_db_file(self._filename, content)

    def plot(self):
        """Display table points on a plot"""
        return self.calib.plot()

    def __info__(self):
        """Print table points (energy, motor position)"""
        title = ["Energy", self._axis.name]
        data = zip(self.calib.x_raw, self.calib.y_raw)
        mystr = tabulate.tabulate(data, headers=title)
        return f"File: {self._filename}\n\n{mystr}"

    def _energy_in_table(self, energy):
        """Check if energy is in table with given tolerance"""
        return numpy.any(
            numpy.isclose(energy, self.calib.get_raw_x(), atol=self._kev_tolerance)
        )

    def setpoint(self):
        """Add current position (energy, motor) to table"""
        current_energy = round(self._axis.tracking._mono.energy_motor.position, 6)
        if self._energy_in_table(current_energy):
            self.calib.delete(x=current_energy)
        self.calib.insert(x=current_energy, y=self._axis.position)

    def delpoint(self, energy=None):
        """Delete current energy from table"""
        if energy is None:
            energy = round(self._axis.tracking._mono.energy_motor.position, 6)
        self.calib.delete(x=energy)


"""
NO IDEA WHY THIS CLASS
"""


class TrackTableMulti:
    def __init__(self, tracked_axes):
        self.__tracked_axes = tracked_axes

    def plot(self, axis):
        """display table points for given axis on a plot"""
        if isinstance(axis, str):
            axis_name = axis
        else:
            axis_name = axis.name
        for axs in self.__tracked_axes():
            if axs.name == axis_name:
                return axs.track_table.plot()
        return None

    def __info__(self):
        """print the calib tables in use for all tracked axes"""
        axes = [axis for axis in self.__tracked_axes() if axis.track_mode == "table"]
        if len(axes) == 0:
            return "No axis currently tracked with table mode"
        title = ["Energy"] + [axis.name for axis in axes]

        energies = [axis.track_table.calib.x_raw for axis in axes]
        energies = numpy.unique(numpy.concatenate(energies))

        def position(axis, energy):
            try:
                return axis.track_table.calib.get_y(energy)
            except Exception:
                return None

        data = []
        for energy in energies:
            data.append([energy] + [position(axis, energy) for axis in axes])

        mystr = tabulate.tabulate(data, headers=title)

        return "\n" + mystr

    def setpoint(self):
        """add current position (energy, motor) to table for all tracked axes"""
        for axis in self.__tracked_axes():
            axis.track_table.setpoint()

    def delpoint(self, energy=None):
        """delete current energy from table for all tracked axes"""
        for axis in self.__tracked_axes():
            axis.track_table.delpoint(energy)

    def save(self):
        """save table calib files to beamline configuration for all tracked axes"""
        for axis in self.__tracked_axes():
            axis.track_table.save()


class TrackerMotor:
    def __init__(self, axis, trackers_controller):
        self._axis = axis
        self._controller = trackers_controller
        self.mode = TrackerMode(axis, trackers_controller)
        self.param_id = TrackerId(axis, trackers_controller)

    def __info__(self):
        mystr = f"Axis           : {self._axis.name}\n"
        if self.state:
            mystr += "Tracking State : ON\n"
        else:
            mystr += "Tracking State : OFF\n"
        mystr += f"Param. id      : {self.param_id.get()}\n"
        mystr += f"Tracking Mode  : {self.mode.get()}\n"
        return mystr

    @property
    def _constant(self):
        return self._controller._parameters[self._axis.name]["constant"]

    @property
    def state(self):
        return self._controller._parameters[self._axis.name]["tracked"].get()

    def on(self):
        self._controller._parameters[self._axis.name]["tracked"].set(True)

    def off(self):
        self._controller._parameters[self._axis.name]["tracked"].set(False)

    def energy2tracker(self, energy):
        return self._controller._energy2tracker(self._axis, energy)

    def tracker2energy(self, track):
        return self._controller._tracker2energy(self._axis, track)

    @property
    def scanning_mode(self):
        return self._controller._get_selected_scanning_mode(self._axis)

    @scanning_mode.setter
    def scanning_mode(self, mode):
        self._controller._set_selected_scanning_mode(self._axis, mode)


class TrackerId:
    def __init__(self, axis, controller):
        self._axis = axis
        self._controller = controller
        param_ids = self._controller._parameters[self._axis.name]["param_id"]
        for param_id in param_ids.keys():
            setattr(self, param_id, functools.partial(self._set_param_id, param_id))

    def __info__(self):
        param_ids = "/".join(
            self._controller._parameters[self._axis.name]["param_id"].keys()
        )
        return f"Selected Param Id: {self.get()} ({param_ids})\n"

    def get(self):
        return self._controller._parameters[self._axis.name]["selected_id"].get()

    def _set_param_id(self, param_id):
        param_ids = self._controller._parameters[self._axis.name]["param_id"]
        if param_ids is not None and param_id in param_ids.keys():
            self._controller._parameters[self._axis.name]["selected_id"].set(param_id)
        else:
            raise ValueError(
                f'TrackerId {self._axis.name}: param_id "{param_id}" not available'
            )


class TrackerMode:
    def __init__(self, axis, controller):
        self._axis = axis
        self._controller = controller

    def __info__(self):
        param_id = self._controller._parameters[self._axis.name]["selected_id"].get()
        modes = "/".join(
            dict(
                filter(
                    lambda x: x[1] is not None,
                    self._controller._parameters[self._axis.name]["param_id"][
                        param_id
                    ].items(),
                )
            ).keys()
        )
        return f"Selected Mode: {self.get()} ({modes})\n"

    def get(self):
        return self._controller._parameters[self._axis.name]["selected_mode"].get()

    def set(self, mode):
        selected_id = self._controller._parameters[self._axis.name]["selected_id"].get()
        modes = self._controller._parameters[self._axis.name]["param_id"][selected_id]
        if modes is not None and mode in modes.keys() and modes[mode] is not None:
            self._controller._parameters[self._axis.name]["selected_mode"].set(mode)
        else:
            raise ValueError(f'Tracker {self._axis.name}: mode "{mode}" not available')
        self._update_master_position()

    def table(self):
        self.set("table")

    def theory(self):
        self.set("theory")

    def polynom(self):
        self.set("polynom")

    def _update_master_position(self):
        master_axis = self._controller._mono._motors["energy_tracker"]
        master_axis.controller.sync_pseudos()


class UndulatorTrackingObject(EnergyTrackingObject):
    def __init__(self, config):
        super().__init__(config)

        # Constants
        me = 9.1093836e-31  # kg
        qe = 1.6021766e-19
        hbar = 1.054572e-34  # m^2 kg/s
        c = 299792458  # m/s

        # Source parameters
        Ering = 6.0  # GeV
        # und_step = 0.05001  ### time quant of acceleration of undulator gap, seconds

        # Calculated constants
        gamma = Ering / 0.511e-3  # Ering is in GeV
        self._c1 = (
            qe / (2.0 * numpy.pi * me * c) / 1e3
        )  # To have E in keV and gap in mm
        self._c2 = (
            8.0 * numpy.pi * hbar * c * gamma**2 / qe
        )  # To have E in keV and gap in mm

    def _ene2gap(self, energy, config):
        harm = float(config["harmonic"])
        Uperiod = float(config["Uperiod"])
        alpha = float(config["alpha"])

        UPPI = Uperiod / numpy.pi
        C2H = self._c2 * harm
        C1A1U = self._c1 * alpha * Uperiod
        gap = UPPI * numpy.log(C1A1U / numpy.sqrt(C2H / (Uperiod * energy) - 2.0))

        return gap

    def _gap2ene(self, gap, config):
        harm = float(config["harmonic"])
        Uperiod = float(config["Uperiod"])
        alpha = float(config["alpha"])

        UPPI = Uperiod / numpy.pi
        C2H = self._c2 * harm
        C1A1U = self._c1 * alpha * Uperiod

        exp = numpy.exp(gap / UPPI)
        num = 2 + numpy.power(C1A1U / exp, 2)
        energy = C2H / (Uperiod * (num))

        return energy


class SimulEnergyTrackingObject(EnergyTrackingObject):
    def __init__(self, config):
        super().__init__(config)

        # Constants
        me = 9.1093836e-31  # kg
        qe = 1.6021766e-19
        hbar = 1.054572e-34  # m^2 kg/s
        c = 299792458  # m/s

        # Source parameters
        Ering = 6.0  # GeV
        self._a_un = 1.909  # added in the formulas as aBr #####TO BE CHECKED####
        # und_step = 0.05001  # time quant of acceleration of undulator gap, seconds

        # Calculated constants
        gamma = Ering / 0.511e-3  # Ering is in GeV
        self._c1 = (
            qe / (2.0 * numpy.pi * me * c) / 1e3
        )  # To have E in keV and gap in mm
        self._c2 = (
            8.0 * numpy.pi * hbar * c * gamma**2 / qe
        )  # To have E in keV and gap in mm

    def _ene2gap(self, energy, config):
        harm = float(config["harmonic"])
        Uperiod = float(config["Uperiod"])

        UPPI = Uperiod / numpy.pi
        C2H = self._c2 * harm
        C1A1U = self._c1 * self._a_un * Uperiod
        gap = UPPI * numpy.log(C1A1U / numpy.sqrt(C2H / (Uperiod * energy) - 2.0))

        return gap

    def _gap2ene(self, gap, config):
        harm = float(config["harmonic"])
        Uperiod = float(config["Uperiod"])

        UPPI = Uperiod / numpy.pi
        C2H = self._c2 * harm
        C1A1U = self._c1 * self._a_un * Uperiod

        exp = numpy.exp(gap / UPPI)
        num = 2 + numpy.power(C1A1U / exp, 2)
        energy = C2H / (Uperiod * (num))

        return energy
