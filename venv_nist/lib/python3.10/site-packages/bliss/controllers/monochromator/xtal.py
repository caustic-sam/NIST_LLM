# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
Definition of classes representing the crystal management. This is part of the
monochromator control.
"""

import copy
import functools
import numpy
import xcalibu

from bliss.common.logtools import log_error
from bliss.config import settings
from bliss.physics.units import ur, units
from bliss.physics.diffraction import CrystalPlane, _get_all_crystals, MultiPlane, hc
from bliss.config.conductor.client import remote_open


class MonochromatorXtals:
    def __init__(self, mono, xtal_list):
        self._mono = mono
        for xtal in xtal_list:
            self._add_label_move_method(xtal)

    def __info__(self):
        info_str = self._mono._xtals.__info__()
        unit = self._mono._motors["bragg"].unit
        bragg_index = info_str.find("Bragg")
        if bragg_index != -1:
            if hasattr(self._mono, "bagg_min_max"):
                min_th, max_th = self._mono.bragg_min_max(unit)
                new_str = f"Bragg [{unit}]: {max_th:.4f} - {min_th:.4f}\n\n"
                info_str = info_str.replace(info_str[bragg_index:], new_str)

        return info_str

    def _xtal_change(self, xtal_name):
        self._mono._xtal_change(xtal_name)
        self._mono._xtals.xtal_sel = xtal_name
        self._mono._motors["energy"].sync_hard()
        xtal = self._mono._xtals.xtal[xtal_name]
        if hasattr(xtal, "en2bragg"):
            unit = self._mono._motors["energy"].unit
            min_en = (xtal.en2bragg.min_x() * ur.J).to(unit).magnitude
            max_en = (xtal.en2bragg.max_x() * ur.J).to(unit).magnitude
            self._mono._motors["energy"].limits = (min_en, max_en)
            if self._mono._motors["energy_tracker"]:
                self._mono._motors["energy_tracker"].limits = (min_en, max_en)

    def change(self, xtal_name):
        self._xtal_change(xtal_name)

    def _change_xtal(self, xtal_name):
        self._mono._xtal_change(xtal_name)
        self._mono._xtals.xtal_sel = xtal_name
        self._mono._motors["energy"].sync_hard()

    def _add_label_move_method(self, xtal_name):
        """Add a method named xtal name to move to the
        corresponding xtal position.
        Args:
        xtal_name (str): Crystal or layer name.
        """

        # name should not start with a number!
        if xtal_name.isidentifier():
            setattr(self, xtal_name, functools.partial(self._change_xtal, xtal_name))
        else:
            log_error(self, f"Xtal: '{xtal_name}' is not a valid python identifier.")


class XtalManager:
    def __init__(self, config):

        self.__config = config
        self.__name = config["name"]

        # Crystal(s) management
        self.all_xtals = self.get_all_xtals()
        xtals = self.config.get("xtals")

        self.xtal_names = []
        self.xtal = {}
        for elem in xtals:
            if "xtal" in elem.keys():
                xtal_name = elem.get("xtal")
                dspacing = elem.get("dspacing", None)
                symbol = self.xtalplane2symbol(xtal_name)
                if symbol not in self.all_xtals:
                    if dspacing is not None:
                        self.xtal[xtal_name] = MultiPlane(distance=dspacing * 1e-10)
                    else:
                        raise RuntimeError("dspacing of Unknown crystals must be given")
                else:
                    self.xtal[xtal_name] = copy.copy(CrystalPlane.fromstring(xtal_name))
                if dspacing is not None:
                    self.xtal[xtal_name].d = dspacing * 1e-10
                self.xtal_names.append(xtal_name)
            elif "multilayer" in elem.keys():
                ml_name = elem.get("multilayer")
                elem["name"] = ml_name
                self.xtal[ml_name] = Multilayer(elem)
                self.xtal_names.append(ml_name)

        def_val = {"xtal_sel": None}
        self.__settings_name = f"XtalManager_{self.name}"
        self.__settings = settings.HashSetting(
            self.__settings_name, default_values=def_val
        )
        if self.settings["xtal_sel"] not in self.xtal_names:
            self.settings["xtal_sel"] = None

    @property
    def name(self):
        return self.__name

    @property
    def config(self):
        return self.__config

    @property
    def settings(self):
        return self.__settings

    @property
    def xtal_sel(self):
        return self.settings["xtal_sel"]

    @xtal_sel.setter
    def xtal_sel(self, xtal):
        if xtal is None or xtal in self.xtal_names:
            self.settings["xtal_sel"] = xtal
        else:
            raise RuntimeError(f"Crystal ({xtal}) not configured")

    def __info__(self):
        if self.xtal_sel is not None:
            xtal_sel = self.xtal[self.xtal_sel]
            if isinstance(xtal_sel, Multilayer):
                info_str = "Multilayer:"
            else:
                info_str = "Crystal:"
        else:
            info_str = "Crystal:"
        info_str += f" {self.xtal_sel} ("
        for xtal in self.xtal_names:
            info_str += f"{xtal} / "
        info_str = info_str[:-3] + ")"
        info_str += "\n"

        if self.xtal_sel is not None:
            if isinstance(xtal_sel, Multilayer):
                ml_str = xtal_sel.__info__()
                info_str += ml_str
            else:
                dspacing = (self.xtal[self.xtal_sel].d * ur.m).to("angstrom")
                info_str += f"dspacing: {dspacing:.5f}\n"

        return info_str

    #
    # Utils
    #

    def get_all_xtals(self):
        xtals = _get_all_crystals()
        all_xtals = []
        for xtal in xtals:
            all_xtals.append(xtal.name)
        return all_xtals

    def xtalplane2symbol(self, xtalplane):
        symbol, plane = "", ""
        for c in xtalplane:
            if c.isdigit() or c.isspace():
                plane += c
            elif c.isalpha():
                symbol += c
        return symbol

    def get_xtals_config(self, key):
        res = None
        xtals = self.config.get("xtals")
        for elem in xtals:
            if "xtal" in elem.keys():
                elem_name = elem.get("xtal")
            elif "multilayer" in elem.keys():
                elem_name = elem.get("multilayer")
            else:
                raise RuntimeError('Neither "xtal" nor "multilayer" keyword in xtal')
            value = elem.get(key, None)
            if value is not None:
                if res is None:
                    res = {}
                try:
                    res[elem_name] = float(elem.get(key))
                except Exception:
                    res[elem_name] = elem.get(key)

        return res

    def bragg_min_max(self, unit="deg"):
        """Get the theoretical min and max bragg angle values.
        Args:
            unit(str): The unit of the value as string ("deg", "mrad", "rad").
                       Default value: "deg"
        Return:
             (tupple): The min and max theoretical value [unit]
        """
        return self.xtal[self.xtal_sel].bragg_min_max(unit)

    #
    # Calculation methods
    #

    def energy2bragg(self, ene):
        """Calculate the bragg angle as function of the energy.
        Args:
            ene(float): Energy [keV]
        Return:
            (float): Bragg angle value [deg]
        """
        if self.xtal_sel is None:
            return numpy.nan
        xtal = self.xtal[self.xtal_sel]
        bragg = xtal.bragg_angle(ene * ur.keV)
        if numpy.isnan(bragg).any():
            return numpy.nan

        # convert radians to degrees
        bragg = bragg.to(ur.deg).magnitude
        return bragg

    def bragg2energy(self, bragg):
        """Calculate the energy as function of the bragg angle
        Args:
            bragg(float): Bragg angle [deg]
        Return:
            (float): Energy [keV]
        """
        if self.xtal_sel is None:
            return numpy.nan
        xtal = self.xtal[self.xtal_sel]
        energy = xtal.bragg_energy(bragg * ur.deg)
        if numpy.isnan(energy.magnitude).any():
            return numpy.nan
        energy = energy.to(ur.keV).magnitude
        return energy

    def get_metadata(self, theta) -> dict:
        if self.xtal_sel is None:
            return {}
        xtal = self.xtal[self.xtal_sel]

        energy = xtal.bragg_energy(theta)
        if numpy.isnan(energy.magnitude).any():
            energy = numpy.nan
        else:
            energy = energy.to(ur.keV).magnitude

        wavelength = xtal.bragg_wavelength(theta)
        if numpy.isnan(wavelength.magnitude).any():
            wavelength = numpy.nan
        else:
            wavelength = wavelength.to(ur.m).magnitude

        mdata = {
            "energy": energy,
            "wavelength": wavelength,
            "crystal": {"d_spacing": xtal.d},
        }

        if isinstance(xtal, CrystalPlane):
            mdata["crystal"]["type"] = xtal.crystal.name
            mdata["crystal"]["reflection"] = tuple(xtal.plane)
        elif isinstance(xtal, Multilayer):
            mdata["crystal"]["type"] = f"multilayer: {xtal.name}"
        else:
            mdata["crystal"]["type"] = "unknown"

        return mdata


class Multilayer:
    """Multilayer crystal handling"""

    def __init__(self, config):
        self.__config = config
        self.__name = config["name"]
        self.thickness1 = self.config.get("thickness1", None)
        self.thickness2 = self.config.get("thickness2", None)
        self.mlab_file = self.config.get("ml_lab_file", None)
        self.ml_file = self.config.get("delta_bar", None)
        self.lut_file = None
        if self.thickness1 is not None and self.thickness2 is not None:
            self.d = ((self.thickness1 + self.thickness2) * 1e-9) * ur.m
            if self.ml_file is not None:
                self.create_lut_from_ml_file()
        else:
            dspacing = self.config.get("dspacing", None)
            if dspacing is not None:
                self.d = (dspacing * 1e-9) * ur.m
            else:
                self.d = None
                self.lut_file = self.config.get("lookup_table", None)
                if self.lut_file is not None:
                    self.create_lut_from_lut_file()
                else:
                    if self.mlab_file is not None:
                        self.create_lut_from_ml_file()
                    else:
                        raise RuntimeError(
                            f"Multilayer {self.name}: Wrong yml configuration"
                        )

    @property
    def name(self):
        return self.__name

    @property
    def config(self):
        return self.__config

    def __info__(self):
        info_str = ""
        if self.thickness1 is not None and self.thickness2 is not None:
            info_str += f"Thickness Material #1: {self.thickness1*ur.nm}\n"
            info_str += f"Thickness Material #2: {self.thickness2*ur.nm}\n"
            dspacing = self.d.to("nm")
            info_str += f"d-spacing: {dspacing}\n"
            if self.ml_file is not None:
                min_en = (self.en2bragg.min_x() * ur.J).to("keV").magnitude
                max_en = (self.en2bragg.max_x() * ur.J).to("keV").magnitude
                min_th = numpy.degrees(self.en2bragg.min_y())
                max_th = numpy.degrees(self.en2bragg.max_y())

                info_str += f"Delta bar file     : {self.ml_file}\n"
                info_str += f"Energy [keV]       : {min_en:.3f} - {max_en:.3f}\n"
                info_str += f"Bragg [deg]        : {max_th:.3f} - {min_th:.3f}\n"
        else:
            if self.d is not None:
                dspacing = self.d.to("nm")
                info_str += f"d-spacing: {dspacing}\n"
            else:
                if self.lut_file is not None:
                    min_en = (self.en2bragg.min_x() * ur.J).to("keV").magnitude
                    max_en = (self.en2bragg.max_x() * ur.J).to("keV").magnitude
                    min_th = numpy.degrees(self.en2bragg.min_y())
                    max_th = numpy.degrees(self.en2bragg.max_y())

                    info_str += f"Lookup table file: {self.lut_file}\n"
                    info_str += f"Energy [keV]: {min_en:.3f} - {max_en:.3f}\n"
                    info_str += f"Bragg [deg]: {max_th:.4f} - {min_th:.4f}\n"
                elif self.mlab_file is not None:
                    min_en = (self.en2bragg.min_x() * ur.J).to("keV").magnitude
                    max_en = (self.en2bragg.max_x() * ur.J).to("keV").magnitude
                    min_th = numpy.degrees(self.en2bragg.min_y())
                    max_th = numpy.degrees(self.en2bragg.max_y())

                    info_str += f"Multilayer_lab file: {self.mlab_file}\n"
                    info_str += f"Energy [keV]       : {min_en:.3f} - {max_en:.3f}\n"
                    info_str += f"Bragg [deg]        : {max_th:.3f} - {min_th:.3f}\n"

                else:
                    raise RuntimeError("THIS ERROR SHOULD NEVER HAPPENED !!!\n")

        return info_str

    def create_lut_from_ml_file(self):
        """Create a lookup table from multilayer file. The file format is the
        standard, defined by the multilayer lab.
        """
        if self.mlab_file is not None:
            with remote_open(self.mlab_file) as ml_file:
                arr = numpy.loadtxt(ml_file, comments="#").transpose()
            arr_energy = numpy.copy((arr[0] * ur.eV).to(ur.J))
            arr_theta = numpy.copy((arr[1] / 1000.0) * ur.rad)

            self.en2bragg = xcalibu.Xcalibu()
            self.en2bragg.set_calib_name(f"{self.name}_bragg")
            self.en2bragg.set_calib_time(0)
            self.en2bragg.set_calib_type("TABLE")
            self.en2bragg.set_reconstruction_method("INTERPOLATION", "linear")
            self.en2bragg.set_raw_x(arr_energy.magnitude)
            self.en2bragg.set_raw_y(arr_theta.magnitude)
            self.en2bragg.check_monotonic()
            self.en2bragg.compute_interpolation()

            arr_flip_theta = numpy.flip(arr_theta)
            arr_flip_energy = numpy.flip(arr_energy)
            self.bragg2en = xcalibu.Xcalibu()
            self.bragg2en.set_calib_name(f"{self.name}_bragg")
            self.bragg2en.set_calib_time(0)
            self.bragg2en.set_calib_type("TABLE")
            self.bragg2en.set_reconstruction_method("INTERPOLATION", "linear")
            self.bragg2en.set_raw_x(arr_flip_theta.magnitude)
            self.bragg2en.set_raw_y(arr_flip_energy.magnitude)
            self.bragg2en.check_monotonic()
            self.bragg2en.compute_interpolation()

    def create_lut_from_lut_file(self):
        """Create a Lookup table from a file. The file should be in format of
        two columns: Energy [eV] bragg_angle [rad]
        """
        if self.lut_file is not None:
            with remote_open(self.lut_file) as lut_file:
                arr = numpy.loadtxt(lut_file, comments="#").transpose()
            arr_energy = numpy.copy(((arr[0] / 1000.0) * ur.keV).to(ur.J))
            arr_theta = numpy.copy(arr[1] * ur.radians)

            self.en2bragg = xcalibu.Xcalibu()
            self.en2bragg.set_calib_name(f"{self.name}_bragg")
            self.en2bragg.set_calib_time(0)
            self.en2bragg.set_calib_type("TABLE")
            self.en2bragg.set_reconstruction_method("INTERPOLATION", "linear")
            self.en2bragg.set_raw_x(arr_energy.magnitude)
            self.en2bragg.set_raw_y(arr_theta.magnitude)
            self.en2bragg.check_monotonic()
            self.en2bragg.compute_interpolation()

            arr_flip_theta = numpy.flip(numpy.copy(arr_theta))
            arr_flip_energy = numpy.flip(numpy.copy(arr_energy))
            self.bragg2en = xcalibu.Xcalibu()
            self.bragg2en.set_calib_name(f"{self.name}_bragg")
            self.bragg2en.set_calib_time(0)
            self.bragg2en.set_calib_type("TABLE")
            self.bragg2en.set_reconstruction_method("INTERPOLATION", "linear")
            self.bragg2en.set_raw_x(arr_flip_theta.magnitude)
            self.bragg2en.set_raw_y(arr_flip_energy.magnitude)
            self.bragg2en.check_monotonic()
            self.bragg2en.compute_interpolation()

    @units(wavelength="m", result="J")
    def wavelength_to_energy(self, wavelength):
        """
        Return photon energy [J] for the given wavelength [m]

        Args:
            wavelength (float): photon wavelength [m]
        Return:
            float: photon energy [J]
        Raises:
            ZeroDivisionError: If the bragg angle = 0.
        """
        if wavelength:
            return hc / wavelength
        raise ZeroDivisionError("Cannot calculate energy for bragg angle = 0")

    @units(energy="J", result="m")
    def energy_to_wavelength(self, energy):
        """
        Return photon wavelength (m) for the given energy (J)

        Args:
            energy (float): photon energy (J)
        Return:
            float: photon wavelength (m)
        """
        if energy:
            return hc / energy
        raise ZeroDivisionError("Cannot calculate energy for bragg angle = 0")

    @units(theta="rad", result="m")
    def bragg_wavelength(self, theta, n=1):
        """
        Return a bragg wavelength (m) for the given theta and distance between
        lattice planes.

        Args:
            theta (float): scattering angle (rad)
            n (int): order of reflection. Non zero positive integer (default: 1)
        Return:
            float: bragg wavelength (m) for the given theta and lattice distance
        """
        return 2.0 * self.d * numpy.sin(theta)

    @units(theta="rad", result="J")
    def bragg_energy(self, theta, n=1):
        """
        Return a bragg energy for the given theta and distance between lattice
        planes.

        Args:
            theta (float): scattering angle (rad)
            n (int): order of reflection. Non zero positive integer (default: 1)
        Return:
            float: bragg energy (J) for the given theta and lattice distance
        """
        if self.mlab_file is None and self.lut_file is None:
            return self.wavelength_to_energy(self.bragg_wavelength(theta, n=n))
        if self.bragg2en.min_x() <= round(theta.magnitude, 16) <= self.bragg2en.max_x():
            return self.bragg2en.get_y(theta.magnitude) * ur.J
        return (numpy.nan) * ur.J

    @units(energy="J", result="rad")
    def bragg_angle(self, energy):
        """
        Return a bragg angle [rad] for the given theta and distance between
        lattice planes.

        Args:
            energy (float): energy [J]
            or
            energy ((numpy array): array of energies (floats) [J]
            d (float): interplanar distance between lattice planes [m]
            n (int): order of reflection. Non zero positive integer (default: 1)
        Return:
            (float): bragg angle [rad] for the given theta and lattice distance
            or
            (numpy array): array of bragg angle [rad]
        """
        if self.mlab_file is None and self.lut_file is None:
            return numpy.arcsin(hc / (2.0 * self.d * energy))
        max_x = self.en2bragg.max_x()
        min_x = self.en2bragg.min_x()
        if numpy.any(min_x > energy.magnitude) and numpy.any(energy.magnitude > max_x):
            return (numpy.nan) * ur.rad
        try:
            # only one value in magnitude
            return self.en2bragg.get_y(energy.magnitude) * ur.rad
        except TypeError:
            # or it is an array
            result = []
            for ene in energy.magnitude:
                result.append(self.en2bragg.get_y(ene))
            return result * ur.rad
