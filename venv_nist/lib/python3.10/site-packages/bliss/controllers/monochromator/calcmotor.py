# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""Calculation motors classes. This is part of the monochromator control."""

import numpy

from bliss.controllers.motor import CalcController


class MonochromatorCalcMotorBase(CalcController):
    """Base class"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._mono = None

        if "approximation" in self.config.config_dict:
            self.approx = float(self.config.get("approximation"))
        else:
            self.approx = 0.0

    def __info__(self):
        info_str = f"CONTROLLER: {self.__class__.__name__}\n"
        return info_str

    def get_axis_info(self, axis):
        """Get the info for axis"""
        info_str = ""
        return info_str

    def _set_mono(self, mono):
        """Define mono property"""
        self._mono = mono

    def _pseudos_are_moving(self):
        """Check if pseudo axis are moving"""
        for axis in self.pseudos:
            if axis.is_moving:
                return True
        return False


class EnergyCalcMotor(MonochromatorCalcMotorBase):
    """Energy Calculation Motor"""

    def calc_from_real(self, real_positions):
        """Calculate the energy from the position of the real motor.
           The real motor value is in the units of the real motor.
           The energy value is in the units of the enetgy motor.
        Args:
            real_positions(dict): Dictionary of the real motor position(s).
        Returns:
            (dict): Dictionary with the energy position(s)
        """
        pseudos_dict = {}

        if self._mono is not None and self._mono._xtals.xtal_sel is not None:
            ene = self._mono.bragg2energy(real_positions["bragg"])
            pseudos_dict["energy"] = ene
        else:
            pseudos_dict["energy"] = numpy.nan
        return pseudos_dict

    def calc_to_real(self, positions_dict):
        """Calculate the position of the real motor from the energy.
           The energy value is in the units of the enetgy motor.
           The real motor value is in the units of the real motor.
        Args:
            positions_dict (dict): Dictionary with the energy position(s)
        Returns:
            (dict): Dictionary of the real motor position(s).
        """
        reals_dict = {}
        if (
            self._mono is not None
            and self._mono._xtals.xtal_sel is not None
            and not numpy.isnan(positions_dict["energy"]).any()
        ):
            reals_dict["bragg"] = self._mono.energy2bragg(positions_dict["energy"])
        return reals_dict


class GratingEnergyCalcMotor(MonochromatorCalcMotorBase):
    """Energy Calculation Motor"""

    def calc_from_real(self, real_positions):
        """Calculate the energy from the position of the real motor.
           The real motor value is in the units of the real motor.
           The energy value is in the units of the enetgy motor.
        Args:
            real_positions(dict): Dictionary of the real motor position(s).
        Returns:
            (dict): Dictionary with the energy position(s)
        """
        pseudos_dict = {}

        if self._mono is not None and self._mono._grating_sel is not None:
            ene = self._mono.angles2energy(real_positions["nu"], real_positions["psi"])
            pseudos_dict["energy"] = ene
        else:
            pseudos_dict["energy"] = numpy.nan
        return pseudos_dict

    def calc_to_real(self, positions_dict):
        """Calculate the position of the real motor from the energy.
           The energy value is in the units of the enetgy motor.
           The real motor value is in the units of the real motor.
        Args:
            positions_dict (dict): Dictionary with the energy position(s)
        Returns:
            (dict): Dictionary of the real motor position(s).
        """
        reals_dict = {}
        if (
            self._mono is not None
            and self._mono._grating_sel is not None
            and not numpy.isnan(positions_dict["energy"]).any()
        ):
            (nu, psi) = self._mono.energy2angles(positions_dict["energy"])
            reals_dict["nu"] = nu
            reals_dict["psi"] = psi
        return reals_dict


class BraggFixExitCalcMotor(MonochromatorCalcMotorBase):
    """
    Bragg Fix Exit Calculation Motor
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._calc_method = self.config.get("calc_method")
        if self._calc_method is None:
            self._calc_method = "xtal"

    def calc_from_real(self, real_positions):

        pseudos_dict = {}

        if self._mono is not None:
            if self._calc_method == "xtal":
                bragg = self._mono.xtal2bragg(real_positions["xtal"])
            else:
                bragg = self._mono.dxtal2bragg(real_positions["xtal"])
            rbragg = real_positions["bragg"]

            if (
                numpy.isclose(bragg, rbragg, atol=self.approx)
            ) or self._pseudos_are_moving():
                pseudos_dict["bragg_fix_exit"] = real_positions["bragg"]
            else:
                pseudos_dict["bragg_fix_exit"] = numpy.nan
        else:
            pseudos_dict["bragg_fix_exit"] = numpy.nan

        return pseudos_dict

    def calc_to_real(self, positions_dict):

        reals_dict = {}

        if (
            self._mono is not None
            and not numpy.isnan(positions_dict["bragg_fix_exit"]).any()
        ):
            reals_dict["bragg"] = positions_dict["bragg_fix_exit"]
            if self._calc_method == "xtal":
                reals_dict["xtal"] = self._mono.bragg2xtal(
                    positions_dict["bragg_fix_exit"]
                )
            else:
                reals_dict["xtal"] = self._mono.bragg2dxtal(
                    positions_dict["bragg_fix_exit"]
                )

        return reals_dict


class EnergyTrackerCalcMotor(MonochromatorCalcMotorBase):
    """
    - all tracker real motors must contain "tracker" in their tag name
    """

    def energy_dial(self, energy_user):
        bragg_motor = self._mono._motors["bragg"]
        bragg_offset = bragg_motor.offset
        bragg_user = self._mono.energy2bragg(energy_user)
        bragg_dial = bragg_user - bragg_offset
        energy_dial = self._mono.bragg2energy(bragg_dial)
        return energy_dial

    def tracker_in_position(self, energy, reals_dict):
        in_pos = True
        for axis in self.reals:
            tag = self._axis_tag(axis)
            if tag.find("tracker") != -1:
                if axis.tracking.state:
                    track = axis.tracking.energy2tracker(energy)
                    rtrack = reals_dict[tag]
                    if not numpy.isclose(track, rtrack, atol=self.approx):
                        in_pos = False
        return in_pos

    def calc_from_real(self, reals_dict):

        pseudos_dict = {}

        energy_dial = self.energy_dial(reals_dict["energy"])

        in_pos = self.tracker_in_position(energy_dial, reals_dict)

        if in_pos or self._pseudos_are_moving():
            pseudos_dict["energy_tracker"] = reals_dict["energy"]
        else:
            pseudos_dict["energy_tracker"] = numpy.nan

        return pseudos_dict

    def calc_to_real(self, pseudos_dict):

        reals_dict = {}

        energy_dial = self.energy_dial(pseudos_dict["energy_tracker"])

        if not numpy.isnan(pseudos_dict["energy_tracker"]).any():
            reals_dict["energy"] = pseudos_dict["energy_tracker"]
            for axis in self.reals:
                tag = self._axis_tag(axis)
                if tag.find("tracker") != -1:
                    if axis.tracking.state:
                        reals_dict[tag] = axis.tracking.energy2tracker(energy_dial)

        return reals_dict


class GratingEnergyTrackerCalcMotor(MonochromatorCalcMotorBase):
    """
    - all tracker real motors must contain "tracker" in their tag name
    """

    def energy_dial(self, energy_user):
        # WARNING
        # This function should re written if grating mono use setE
        # This methos should be in monochromator object
        return energy_user

    def tracker_in_position(self, energy, reals_dict):
        in_pos = True
        for axis in self.reals:
            tag = self._axis_tag(axis)
            if tag.find("tracker") != -1:
                if axis.tracking.state:
                    track = axis.tracking.energy2tracker(energy)
                    rtrack = reals_dict[tag]
                    if not numpy.isclose(track, rtrack, atol=self.approx):
                        in_pos = False
        return in_pos

    def calc_from_real(self, reals_dict):

        pseudos_dict = {}

        energy_dial = self.energy_dial(reals_dict["energy"])

        in_pos = self.tracker_in_position(energy_dial, reals_dict)

        if in_pos or self._pseudos_are_moving():
            pseudos_dict["energy_tracker"] = reals_dict["energy"]
        else:
            pseudos_dict["energy_tracker"] = numpy.nan

        return pseudos_dict

    def calc_to_real(self, pseudos_dict):

        reals_dict = {}

        energy_dial = self.energy_dial(pseudos_dict["energy_tracker"])

        if not numpy.isnan(pseudos_dict["energy_tracker"]).any():
            reals_dict["energy"] = pseudos_dict["energy_tracker"]
            for axis in self.reals:
                tag = self._axis_tag(axis)
                if tag.find("tracker") != -1:
                    if axis.tracking.state:
                        reals_dict[tag] = axis.tracking.energy2tracker(energy_dial)

        return reals_dict
